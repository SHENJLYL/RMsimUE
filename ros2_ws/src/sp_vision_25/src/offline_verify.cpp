#include "sp_vision_25/detector.hpp"
#include "sp_vision_25/solver_tracker.hpp"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>
#include <opencv2/imgcodecs.hpp>

int main(int argc, char ** argv)
{
  using namespace sp_vision_25;

  const std::filesystem::path assets_dir = (argc > 1)
    ? std::filesystem::path(argv[1])
    : std::filesystem::path("D:/simulator/ros2_ws/src/sp_vision_25/assets");
  const std::filesystem::path frames_dir = (argc > 2)
    ? std::filesystem::path(argv[2])
    : std::filesystem::path("D:/simulator/artifacts/unreal/armor-camera-20261125-neutral");

  const auto det_json_path = frames_dir / "detection" / "detections.json";
  std::ifstream ifs(det_json_path);
  if (!ifs) {
    std::cerr << "Failed to open " << det_json_path << std::endl;
    return 2;
  }
  nlohmann::json det_ref;
  ifs >> det_ref;

  SpVisionYoloV8Detector detector(assets_dir, 0.42f, 0.45f, 0.72f, 4);
  BallisticAimer aimer(24.5);
  const cv::Matx33d K(1108.51, 0.0, 640.0, 0.0, 1108.51, 360.0, 0.0, 0.0, 1.0);

  std::vector<std::string> fnames;
  for (auto it = det_ref["frames"].begin(); it != det_ref["frames"].end(); ++it) {
    fnames.push_back(it.key());
  }
  std::sort(fnames.begin(), fnames.end());

  int total = 0;
  int passed = 0;
  int sentry_total = 0;
  int sentry_passed = 0;

  const auto t0 = std::chrono::steady_clock::now();
  for (const auto & fname : fnames) {
    const auto img_path = frames_dir / fname;
    if (!std::filesystem::is_regular_file(img_path)) continue;
    cv::Mat bgr = cv::imread(img_path.string());
    if (bgr.empty()) continue;

    ++total;
    const bool is_sentry = (fname.rfind("sentry-", 0) == 0);
    if (is_sentry) ++sentry_total;
    const std::string exp_color =
      (fname.find("-blue-") != std::string::npos || fname.find("by-blue") != std::string::npos)
      ? "blue" : "red";
    const std::string exp_name = is_sentry ? "sentry" : "outpost";

    const auto dets = detector.detect(bgr);
    const ArmorDetection * best = nullptr;
    for (const auto & d : dets) {
      if (d.color == exp_color && d.name == exp_name) {
        if (!best || d.confidence > best->confidence) {
          best = &d;
        }
      }
    }

    if (best != nullptr) {
      const auto p_cam = solve_armor_pnp(best->keypoints, best->armor_type, K);
      if (p_cam.has_value()) {
        const auto sol = aimer.solve_cam(*p_cam, 0.0, 0.0, 2, is_sentry ? "vehicle" : "outpost");
        ++passed;
        if (is_sentry) ++sentry_passed;
        std::cout << "OK   " << std::left << std::setw(34) << fname
                  << " | " << best->color << "/" << best->name
                  << " conf=" << std::fixed << std::setprecision(2) << best->confidence
                  << " | dist=" << sol.distance_m << "m tof=" << static_cast<int>(sol.tof_s * 1000.0) << "ms"
                  << " | yaw=" << std::showpos << rad2deg(sol.target_yaw)
                  << "deg pitch=" << rad2deg(sol.target_pitch) << "deg" << std::noshowpos
                  << " shoot=" << (sol.shoot ? "true" : "false") << "\n";
        continue;
      }
    }
    std::cout << "MISS " << std::left << std::setw(34) << fname << " | got=[";
    for (size_t i = 0; i < dets.size(); ++i) {
      if (i) std::cout << ", ";
      std::cout << "(" << dets[i].color << "," << dets[i].name << "," << std::fixed << std::setprecision(2) << dets[i].confidence << ")";
    }
    std::cout << "]\n";
  }
  const auto t1 = std::chrono::steady_clock::now();
  const double elapsed_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

  std::cout << "\nSUMMARY [C++17 backend=" << detector.backend_name() << "]: "
            << "total=" << passed << "/" << total
            << " (" << std::fixed << std::setprecision(1) << (100.0 * passed / std::max(1, total)) << "%), "
            << "sentry=" << sentry_passed << "/" << sentry_total
            << " (" << (100.0 * sentry_passed / std::max(1, sentry_total)) << "%), "
            << "avg=" << (elapsed_ms / std::max(1, total)) << " ms/frame\n";
  return (sentry_passed == sentry_total && passed >= 15) ? 0 : 1;
}
