#include <windows.h>
#include <dwmapi.h>

#include <OgreRenderTarget.h>
#include <OgreRenderTargetListener.h>
#include <OgreViewport.h>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include "rviz_common/display_group.hpp"
#include "rviz_common/frame_manager_iface.hpp"
#include "rviz_default_plugins/displays/map/map_display.hpp"
#include <QPointer>
#include <cstdio>
#include "pluginlib/class_list_macros.hpp"
#include "rviz_common/display.hpp"
#include "rviz_common/display_context.hpp"
#include "rviz_common/render_panel.hpp"
#include "rviz_common/view_manager.hpp"
#include "rviz_rendering/render_window.hpp"

namespace rmsim_rviz_coverage
{
namespace
{
bool fully_covered(HWND observer, DWORD ue_pid)
{
  const HWND foreground = GetForegroundWindow();
  if (!observer || !foreground || observer == foreground || !IsWindowVisible(foreground) ||
      IsIconic(foreground) || (GetWindowLongW(observer, GWL_EXSTYLE) & WS_EX_TOPMOST)) {
    return false;
  }
  DWORD pid = 0;
  if (!GetWindowThreadProcessId(foreground, &pid) || pid != ue_pid) {
    return false;
  }
  const LONG style = GetWindowLongW(foreground, GWL_STYLE);
  const LONG extended_style = GetWindowLongW(foreground, GWL_EXSTYLE);
  RECT region{};
  DWORD cloaked = 0;
  if (!(style & WS_POPUP) || (style & WS_CAPTION) ||
      (extended_style & (WS_EX_LAYERED | WS_EX_TRANSPARENT)) ||
      GetWindowRgnBox(foreground, &region) != ERROR ||
      FAILED(DwmGetWindowAttribute(foreground, DWMWA_CLOAKED, &cloaked, sizeof(cloaked))) || cloaked) {
    return false;
  }
  RECT outer{}, inner{};
  return SUCCEEDED(DwmGetWindowAttribute(foreground, DWMWA_EXTENDED_FRAME_BOUNDS, &outer, sizeof(outer))) &&
         SUCCEEDED(DwmGetWindowAttribute(observer, DWMWA_EXTENDED_FRAME_BOUNDS, &inner, sizeof(inner))) &&
         outer.left <= inner.left && inner.left < inner.right && inner.right <= outer.right &&
         outer.top <= inner.top && inner.top < inner.bottom && inner.bottom <= outer.bottom;
}
}

class FullscreenCoverage final : public rviz_common::Display, private Ogre::RenderTargetListener
{
public:
  ~FullscreenCoverage() override { detach(); }

protected:
  void onInitialize() override
  {
    bool valid = false;
    ue_pid_ = qEnvironmentVariableIntValue("RMSIM_RVIZ_OCCLUDER_PID", &valid);
    if (!valid) {ue_pid_ = 0;}
    diagnostic_ = qEnvironmentVariableIsSet("RMSIM_RVIZ_COVERAGE_TIMING");
    disabled_ = qEnvironmentVariableIsSet("RMSIM_RVIZ_RENDER_COVERED_DIAGNOSTIC");
    ready_path_ = qEnvironmentVariable("RMSIM_RVIZ_READY_PATH");
    telemetry_time_.start();
  }

  void onDisable() override { detach(); }

  void update(float, float) override
  {
    if (ue_pid_ <= 0) {return;}
    auto* window = context_->getViewManager()->getRenderPanel()->getRenderWindow();
    auto* viewport = rviz_rendering::RenderWindowOgreAdapter::getOgreViewport(window);
    if (!viewport) {return;}
    if (window_ != window) {
      detach();
      window_ = window;
      viewport->getTarget()->addListener(this);
    }
    auto* target = viewport->getTarget();
    HWND observer = GetAncestor(reinterpret_cast<HWND>(window->winId()), GA_ROOT);
    const bool covered = fully_covered(observer, static_cast<DWORD>(ue_pid_));
    if (!ready_path_.isEmpty()) {
      ready_frame_ = false;
      auto * group = context_->getRootDisplayGroup();
      bool local_map = false, robot_axes = false;
      map_width_ = map_height_ = 0;
      for (int i = 0; i < group->numDisplays(); ++i) {
        auto * display = group->getDisplayAt(i);
        if (!display || !display->isEnabled()) {continue;}
        if (display->getName() == "Estimated robot pose") {robot_axes = true;}
        auto * map = dynamic_cast<rviz_default_plugins::displays::MapDisplay *>(display);
        if (!map || !map->getWidth() || !map->getHeight()) {continue;}
        if (map->getName() == "Navigation map") {
          map_width_ = static_cast<int>(map->getWidth());
          map_height_ = static_cast<int>(map->getHeight());
        } else if (map->getName() == "Local obstacle costs") {local_map = true;}
      }
      Ogre::Vector3 position;
      Ogre::Quaternion rotation;
      ready_frame_ = map_width_ > 0 && local_map && robot_axes && !covered &&
        window->isExposed() && IsWindowVisible(observer) && !IsIconic(observer) &&
        context_->getFrameManager()->getTransform("aim_sentry/base_link",
          rclcpp::Time(0, 0, context_->getClock()->get_clock_type()), position, rotation);
      if (ready_frame_) {context_->queueRender();}
    }
    const bool pause = covered && !disabled_;
    if (pause != paused_) {
      if (pause) {
        previously_automatic_ = target->isAutoUpdated();
        target->setAutoUpdated(false);
      } else {
        target->setAutoUpdated(previously_automatic_);
        context_->queueRender();
      }
      paused_ = pause;
    }
    ++updates_;
    if (diagnostic_ && telemetry_time_.elapsed() >= 1000) {
      std::fprintf(stderr, "RMSIM_RVIZ_COVERAGE covered=%d paused=%d updates=%llu draws=%llu\n",
        covered, paused_, updates_, draws_);
      std::fflush(stderr);
      telemetry_time_.restart();
    }
  }

private:
  void postRenderTargetUpdate(const Ogre::RenderTargetEvent&) override
  {
    ++draws_;
    if (!ready_frame_ || ready_path_.isEmpty()) {return;}
    LARGE_INTEGER counter, frequency;
    const auto clock_ns = context_->getClock()->now().nanoseconds();
    if (clock_ns <= 0 || !QueryPerformanceCounter(&counter) || !QueryPerformanceFrequency(&frequency)) {return;}
    QJsonObject receipt{{"pid", static_cast<int>(GetCurrentProcessId())}, {"draws", static_cast<double>(draws_)},
      {"map_width", map_width_}, {"map_height", map_height_}, {"local_map", true}, {"robot_tf", true},
      {"clock_ns", static_cast<double>(clock_ns)},
      {"monotonic_s", static_cast<double>(counter.QuadPart) / frequency.QuadPart}};
    QSaveFile file(ready_path_);
    const auto bytes = QJsonDocument(receipt).toJson(QJsonDocument::Compact);
    if (file.open(QIODevice::WriteOnly) && file.write(bytes) == bytes.size() && file.commit()) {
      ready_path_.clear();
      ready_frame_ = false;
    }
  }

  void detach()
  {
    if (window_) {
      auto* viewport = rviz_rendering::RenderWindowOgreAdapter::getOgreViewport(window_.data());
      if (viewport) {
        auto* target = viewport->getTarget();
        if (paused_) {target->setAutoUpdated(previously_automatic_);}
        target->removeListener(this);
      }
    }
    window_.clear();
    paused_ = false;
  }

  QPointer<rviz_rendering::RenderWindow> window_;
  QElapsedTimer telemetry_time_;
  QString ready_path_;
  int map_width_ = 0, map_height_ = 0;
  bool ready_frame_ = false;
  int ue_pid_ = 0;
  bool diagnostic_ = false, disabled_ = false, paused_ = false, previously_automatic_ = false;
  unsigned long long updates_ = 0, draws_ = 0;
};
}

PLUGINLIB_EXPORT_CLASS(rmsim_rviz_coverage::FullscreenCoverage, rviz_common::Display)
