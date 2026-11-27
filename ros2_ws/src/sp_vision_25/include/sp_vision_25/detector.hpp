#pragma once

#include <array>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include <opencv2/core.hpp>
#include <opencv2/dnn.hpp>

#ifdef SP_VISION_HAS_OPENVINO
#include <openvino/openvino.hpp>
#endif

namespace sp_vision_25 {

constexpr int kNumArmorClasses = 9;
inline const std::array<std::string, kNumArmorClasses> kArmorNames = {
  "one", "two", "three", "four", "five", "sentry", "outpost", "base", "not_armor"
};
inline const std::array<std::string, 2> kArmorColors = {"blue", "red"};

struct ArmorDetection {
  std::string color;                   // "blue" | "red"
  std::string name;                    // "sentry" | "outpost" | "base" | "one" | ...
  std::string armor_type;              // "small" | "big"
  float confidence{0.0f};              // classifier / sticker confidence
  float yolo_score{0.0f};
  cv::Rect box;
  std::array<cv::Point2f, 4> keypoints{};  // [tl, tr, br, bl] in pixel coords
  cv::Point2f center{0.0f, 0.0f};
  float bar_height_px{0.0f};
  bool outpost_marker{false};
};

std::array<cv::Point2f, 4> sort_keypoints(const std::array<cv::Point2f, 4> & kps);
cv::Mat crop_armor_pattern(const cv::Mat & bgr, const std::array<cv::Point2f, 4> & kps);

class SpVisionYoloV8Detector {
public:
  SpVisionYoloV8Detector(
    const std::filesystem::path & assets_dir,
    float score_threshold = 0.42f,
    float nms_threshold = 0.45f,
    float min_classify_conf = 0.60f,
    int num_threads = 4);

  std::vector<ArmorDetection> detect(
    const cv::Mat & bgr,
    const std::string & target_color = "",
    bool prefer_outpost = false,
    bool verify_sim_lamp_color = false);

  std::string backend_name() const { return backend_name_; }
  bool has_outpost_reference() const { return !outpost_reference_.empty(); }

private:
  std::pair<std::string, float> classify(const cv::Mat & pattern);
  std::vector<ArmorDetection> detect_hsv_lightbars(
    const cv::Mat & bgr, const cv::Mat & hsv,
    const std::string & color,
    bool prefer_outpost);
  cv::Mat run_yolo_inference(const cv::Mat & letterbox_bgr);

  float score_threshold_;
  float nms_threshold_;
  float min_classify_conf_;
  std::string backend_name_;

  cv::dnn::Net yolo_net_;
  cv::dnn::Net cls_net_;
  cv::Mat outpost_reference_;  // CV_8UC1 (44 x 32) binary mask {0, 1}

#ifdef SP_VISION_HAS_OPENVINO
  bool use_openvino_{false};
  std::unique_ptr<ov::Core> ov_core_;
  ov::CompiledModel ov_compiled_;
  ov::InferRequest ov_infer_;
#endif
};

}  // namespace sp_vision_25
