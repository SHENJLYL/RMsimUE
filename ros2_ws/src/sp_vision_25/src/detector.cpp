#include "sp_vision_25/detector.hpp"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <numeric>
#include <stdexcept>

#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

namespace sp_vision_25 {
namespace {

std::vector<uchar> read_file_bytes(const std::filesystem::path & path)
{
  std::ifstream ifs(path, std::ios::binary | std::ios::ate);
  if (!ifs) {
    throw std::runtime_error("Failed to open file: " + path.string());
  }
  const std::streamsize size = ifs.tellg();
  if (size <= 0) {
    throw std::runtime_error("Empty file: " + path.string());
  }
  ifs.seekg(0, std::ios::beg);
  std::vector<uchar> buf(static_cast<size_t>(size));
  if (!ifs.read(reinterpret_cast<char *>(buf.data()), size)) {
    throw std::runtime_error("Failed to read file: " + path.string());
  }
  return buf;
}

bool is_numbered_vehicle(const std::string & name)
{
  return name == "one" || name == "two" || name == "three" || name == "four" || name == "five";
}

struct MarkerResult {
  cv::Mat normalized;  // CV_8UC1 44x32 values {0, 1}
  int width{0};
  int height{0};
};

MarkerResult marker_mask(const cv::Mat & image)
{
  MarkerResult res;
  if (image.empty() || image.channels() < 3) {
    return res;
  }
  cv::Mat gray;
  cv::cvtColor(image, gray, cv::COLOR_BGR2GRAY);

  cv::Mat mask(image.rows, image.cols, CV_8UC1, cv::Scalar(0));
  int min_x = image.cols;
  int min_y = image.rows;
  int max_x = -1;
  int max_y = -1;

  for (int y = 0; y < image.rows; ++y) {
    const uchar * g_row = gray.ptr<uchar>(y);
    const cv::Vec3b * bgr_row = image.ptr<cv::Vec3b>(y);
    uchar * m_row = mask.ptr<uchar>(y);
    for (int x = 0; x < image.cols; ++x) {
      if (g_row[x] >= 150) {
        const cv::Vec3b & px = bgr_row[x];
        const uchar cmax = std::max({px[0], px[1], px[2]});
        const uchar cmin = std::min({px[0], px[1], px[2]});
        if (static_cast<int>(cmax) - static_cast<int>(cmin) <= 70) {
          m_row[x] = 255;
          if (x < min_x) min_x = x;
          if (x > max_x) max_x = x;
          if (y < min_y) min_y = y;
          if (y > max_y) max_y = y;
        }
      }
    }
  }

  if (max_x < min_x || max_y < min_y) {
    return res;
  }
  const int width = max_x - min_x + 1;
  const int height = max_y - min_y + 1;
  res.width = width;
  res.height = height;
  if (std::min(width, height) < 4) {
    return res;
  }

  cv::Mat roi = mask(cv::Rect(min_x, min_y, width, height));
  cv::Mat resized;
  cv::resize(roi, resized, cv::Size(32, 44), 0.0, 0.0, cv::INTER_AREA);
  cv::threshold(resized, res.normalized, 109, 1, cv::THRESH_BINARY);
  return res;
}

float outpost_marker_score(
  const cv::Mat & frame,
  const cv::Mat & reference,
  float x1,
  float x2,
  float center_y,
  float height)
{
  const float spacing = std::abs(x2 - x1);
  const int left = std::max(0, static_cast<int>(std::lround(std::min(x1, x2) + 0.16f * spacing)));
  const int right = std::min(frame.cols, static_cast<int>(std::lround(std::max(x1, x2) - 0.16f * spacing)));
  const int top = std::max(0, static_cast<int>(std::lround(center_y - 0.92f * height)));
  const int bottom = std::min(frame.rows, static_cast<int>(std::lround(center_y + 0.92f * height)));
  if (left >= right || top >= bottom) {
    return 0.0f;
  }

  const MarkerResult m = marker_mask(frame(cv::Rect(left, top, right - left, bottom - top)));
  if (m.normalized.empty()) {
    return 0.0f;
  }
  const float w_ratio = static_cast<float>(m.width) / std::max(1.0f, spacing);
  const float h_ratio = static_cast<float>(m.height) / std::max(1.0f, height);
  if (w_ratio < 0.32f || w_ratio > 0.65f || h_ratio < 0.9f || h_ratio > 1.85f) {
    return 0.0f;
  }

  int inter = 0;
  int uni = 0;
  for (int y = 0; y < m.normalized.rows; ++y) {
    const uchar * a = m.normalized.ptr<uchar>(y);
    const uchar * b = reference.ptr<uchar>(y);
    for (int x = 0; x < m.normalized.cols; ++x) {
      const bool va = a[x] != 0;
      const bool vb = b[x] != 0;
      if (va && vb) ++inter;
      if (va || vb) ++uni;
    }
  }
  return static_cast<float>(inter) / static_cast<float>(std::max(1, uni));
}

}  // namespace

std::array<cv::Point2f, 4> sort_keypoints(const std::array<cv::Point2f, 4> & kps)
{
  std::array<cv::Point2f, 4> pts = kps;
  std::sort(pts.begin(), pts.end(), [](const cv::Point2f & a, const cv::Point2f & b) {
    return a.y < b.y;
  });
  std::array<cv::Point2f, 2> top = {pts[0], pts[1]};
  std::array<cv::Point2f, 2> bot = {pts[2], pts[3]};
  if (top[0].x > top[1].x) std::swap(top[0], top[1]);
  if (bot[0].x > bot[1].x) std::swap(bot[0], bot[1]);
  return {top[0], top[1], bot[1], bot[0]};
}

cv::Mat crop_armor_pattern(const cv::Mat & bgr, const std::array<cv::Point2f, 4> & kps)
{
  const cv::Point2f mid_l = (kps[0] + kps[3]) * 0.5f;
  const cv::Point2f vec_l = (kps[3] - kps[0]) * 1.125f;
  const cv::Point2f tl = mid_l - vec_l;
  const cv::Point2f bl = mid_l + vec_l;

  const cv::Point2f mid_r = (kps[2] + kps[1]) * 0.5f;
  const cv::Point2f vec_r = (kps[2] - kps[1]) * 1.125f;
  const cv::Point2f tr = mid_r - vec_r;
  const cv::Point2f br = mid_r + vec_r;

  const int x0 = std::max(0, static_cast<int>(std::min(tl.x, bl.x)));
  const int y0 = std::max(0, static_cast<int>(std::min(tl.y, tr.y)));
  const int x1 = std::min(bgr.cols, static_cast<int>(std::max(tr.x, br.x)));
  const int y1 = std::min(bgr.rows, static_cast<int>(std::max(bl.y, br.y)));
  if (x1 <= x0 + 2 || y1 <= y0 + 2) {
    return cv::Mat();
  }
  return bgr(cv::Rect(x0, y0, x1 - x0, y1 - y0));
}

SpVisionYoloV8Detector::SpVisionYoloV8Detector(
  const std::filesystem::path & assets_dir,
  float score_threshold,
  float nms_threshold,
  float min_classify_conf,
  int num_threads)
: score_threshold_(score_threshold),
  nms_threshold_(nms_threshold),
  min_classify_conf_(min_classify_conf),
  backend_name_("opencv_dnn_cpu")
{
  const auto xml_path = assets_dir / "yolov8.xml";
  const auto bin_path = assets_dir / "yolov8.bin";
  const auto cls_path = assets_dir / "tiny_resnet.onnx";

  if (!std::filesystem::is_regular_file(xml_path) ||
      !std::filesystem::is_regular_file(bin_path) ||
      !std::filesystem::is_regular_file(cls_path))
  {
    throw std::runtime_error("Missing sp_vision_25 weights in: " + assets_dir.string());
  }

  cv::setNumThreads(std::max(1, num_threads));

  cls_net_ = cv::dnn::readNetFromONNX(cls_path.string());

#ifdef SP_VISION_HAS_OPENVINO
  try {
    ov_core_ = std::make_unique<ov::Core>();
    auto model = ov_core_->read_model(xml_path.string(), bin_path.string());
    ov_compiled_ = ov_core_->compile_model(
      model, "CPU",
      ov::hint::performance_mode(ov::hint::PerformanceMode::LATENCY),
      ov::inference_num_threads(std::max(1, num_threads)));
    ov_infer_ = ov_compiled_.create_infer_request();
    use_openvino_ = true;
    backend_name_ = "openvino_native_cpu";
  } catch (const std::exception &) {
    use_openvino_ = false;
  }
#endif

  if (backend_name_ == "opencv_dnn_cpu") {
    yolo_net_ = cv::dnn::readNetFromModelOptimizer(xml_path.string(), bin_path.string());
  }

  const auto sticker_path = assets_dir / "outpost_sticker.png";
  if (std::filesystem::is_regular_file(sticker_path)) {
    const auto img_bytes = read_file_bytes(sticker_path);
    cv::Mat img = cv::imdecode(img_bytes, cv::IMREAD_COLOR);
    if (!img.empty()) {
      outpost_reference_ = marker_mask(img).normalized;
    }
  }
}

std::pair<std::string, float> SpVisionYoloV8Detector::classify(const cv::Mat & pattern)
{
  cv::Mat gray;
  cv::cvtColor(pattern, gray, cv::COLOR_BGR2GRAY);
  const int gh = gray.rows;
  const int gw = gray.cols;
  const double scale = std::min(32.0 / static_cast<double>(gw), 32.0 / static_cast<double>(gh));
  const int nw = std::max(1, static_cast<int>(gw * scale));
  const int nh = std::max(1, static_cast<int>(gh * scale));

  cv::Mat canvas = cv::Mat::zeros(32, 32, CV_8UC1);
  cv::Mat resized;
  cv::resize(gray, resized, cv::Size(nw, nh));
  resized.copyTo(canvas(cv::Rect(0, 0, nw, nh)));

  cv::Mat blob = cv::dnn::blobFromImage(
    canvas, 1.0 / 255.0, cv::Size(32, 32), cv::Scalar(), false, false, CV_32F);
  cls_net_.setInput(blob);
  cv::Mat logits_mat = cls_net_.forward();
  const int n = static_cast<int>(logits_mat.total());
  const float * logits = logits_mat.ptr<float>(0);
  if (n < kNumArmorClasses) {
    return {"not_armor", 0.0f};
  }

  float max_val = logits[0];
  for (int i = 1; i < kNumArmorClasses; ++i) {
    if (logits[i] > max_val) max_val = logits[i];
  }
  std::array<float, kNumArmorClasses> probs{};
  float sum = 0.0f;
  for (int i = 0; i < kNumArmorClasses; ++i) {
    probs[i] = std::exp(logits[i] - max_val);
    sum += probs[i];
  }
  int best_idx = 0;
  float best_prob = probs[0] / (sum + 1e-12f);
  for (int i = 1; i < kNumArmorClasses; ++i) {
    const float p = probs[i] / (sum + 1e-12f);
    if (p > best_prob) {
      best_prob = p;
      best_idx = i;
    }
  }
  return {kArmorNames[best_idx], best_prob};
}

cv::Mat SpVisionYoloV8Detector::run_yolo_inference(const cv::Mat & letterbox_bgr)
{
  cv::Mat blob = cv::dnn::blobFromImage(
    letterbox_bgr, 1.0 / 255.0, cv::Size(416, 416), cv::Scalar(), true, false, CV_32F);

#ifdef SP_VISION_HAS_OPENVINO
  if (use_openvino_) {
    ov::Tensor input_tensor = ov_infer_.get_input_tensor(0);
    std::memcpy(input_tensor.data<float>(), blob.ptr<float>(0), blob.total() * sizeof(float));
    ov_infer_.infer();
    const ov::Tensor out_tensor = ov_infer_.get_output_tensor(0);
    const auto shape = out_tensor.get_shape();
    if (shape.size() == 3) {
      const int dims[3] = {
        static_cast<int>(shape[0]),
        static_cast<int>(shape[1]),
        static_cast<int>(shape[2])};
      cv::Mat out(3, dims, CV_32F);
      std::memcpy(out.ptr<float>(0), out_tensor.data<const float>(), out_tensor.get_size() * sizeof(float));
      return out;
    }
  }
#endif

  yolo_net_.setInput(blob);
  return yolo_net_.forward();
}

std::vector<ArmorDetection> SpVisionYoloV8Detector::detect_hsv_lightbars(
  const cv::Mat & bgr,
  const std::string & color,
  bool prefer_outpost)
{
  cv::Mat hsv;
  cv::cvtColor(bgr, hsv, cv::COLOR_BGR2HSV);
  cv::Mat mask;
  if (color == "red") {
    cv::Mat m1, m2;
    cv::inRange(hsv, cv::Scalar(0, 115, 135), cv::Scalar(24, 255, 255), m1);
    cv::inRange(hsv, cv::Scalar(165, 115, 135), cv::Scalar(180, 255, 255), m2);
    cv::bitwise_or(m1, m2, mask);
  } else {
    cv::inRange(hsv, cv::Scalar(86, 105, 135), cv::Scalar(135, 255, 255), mask);
  }

  cv::Mat labels, stats, centroids;
  const int num_labels = cv::connectedComponentsWithStats(mask, labels, stats, centroids, 8);

  struct Bar {
    float cx, cy, w, h;
  };
  std::vector<Bar> bars;
  bars.reserve(32);
  for (int i = 1; i < num_labels; ++i) {
    const int x = stats.at<int>(i, cv::CC_STAT_LEFT);
    const int y = stats.at<int>(i, cv::CC_STAT_TOP);
    const int w = stats.at<int>(i, cv::CC_STAT_WIDTH);
    const int h = stats.at<int>(i, cv::CC_STAT_HEIGHT);
    const int area = stats.at<int>(i, cv::CC_STAT_AREA);
    if (area < 3 || h < 3 || w <= 0) {
      continue;
    }
    const float aspect = static_cast<float>(h) / static_cast<float>(w);
    if (aspect < 1.25f || aspect > 22.0f) {
      continue;
    }
    bars.push_back({x + w * 0.5f, y + h * 0.5f, static_cast<float>(w), static_cast<float>(h)});
  }

  if (bars.size() < 2) {
    return {};
  }

  struct Candidate {
    float area;
    float x1, y1, h1;
    float x2, y2, h2;
  };
  std::vector<Candidate> candidates;
  for (size_t i = 0; i < bars.size(); ++i) {
    const auto & b1 = bars[i];
    for (size_t j = i + 1; j < bars.size(); ++j) {
      const auto & b2 = bars[j];
      const float min_bar_aspect = prefer_outpost ? 1.45f : 1.75f;
      if (std::max(b1.h / b1.w, b2.h / b2.w) < min_bar_aspect) {
        continue;
      }
      const float avg_h = 0.5f * (b1.h + b2.h);
      if (std::abs(b1.h - b2.h) / std::max(1.0f, avg_h) > 0.50f) {
        continue;
      }
      const float dx = std::abs(b1.cx - b2.cx);
      const float dy = std::abs(b1.cy - b2.cy);
      if (dy > avg_h * 0.70f) {
        continue;
      }
      const float ratio = dx / std::max(1.0f, avg_h);
      if (ratio < 1.15f || ratio > 5.4f) {
        continue;
      }
      candidates.push_back({dx * avg_h, b1.cx, b1.cy, b1.h, b2.cx, b2.cy, b2.h});
    }
  }

  std::sort(candidates.begin(), candidates.end(), [](const Candidate & a, const Candidate & b) {
    return a.area > b.area;
  });
  if (candidates.size() > 12) {
    candidates.resize(12);
  }

  const int h_img = bgr.rows;
  const int w_img = bgr.cols;
  std::vector<ArmorDetection> out;

  for (auto c : candidates) {
    if (c.x1 > c.x2) {
      std::swap(c.x1, c.x2);
      std::swap(c.y1, c.y2);
      std::swap(c.h1, c.h2);
    }
    const float avg_h = 0.5f * (c.h1 + c.h2);
    const float cx_p = 0.5f * (c.x1 + c.x2);
    const float cy_p = 0.5f * (c.y1 + c.y2);
    const std::array<cv::Point2f, 4> kps = {
      cv::Point2f(c.x1, c.y1 - 0.5f * c.h1),
      cv::Point2f(c.x2, c.y2 - 0.5f * c.h2),
      cv::Point2f(c.x2, c.y2 + 0.5f * c.h2),
      cv::Point2f(c.x1, c.y1 + 0.5f * c.h1),
    };
    const cv::Rect box(
      static_cast<int>(c.x1),
      static_cast<int>(std::min(c.y1 - 0.5f * c.h1, c.y2 - 0.5f * c.h2)),
      static_cast<int>(c.x2 - c.x1),
      static_cast<int>(avg_h));

    if (!outpost_reference_.empty()) {
      const float score = outpost_marker_score(bgr, outpost_reference_, c.x1, c.x2, cy_p, avg_h);
      const float marker_thr = prefer_outpost ? 0.36f : 0.50f;
      if (score >= marker_thr) {
        ArmorDetection det;
        det.color = color;
        det.name = "outpost";
        det.armor_type = "small";
        det.confidence = score;
        det.yolo_score = score;
        det.box = box;
        det.keypoints = kps;
        det.center = cv::Point2f(cx_p, cy_p);
        det.bar_height_px = avg_h;
        out.push_back(std::move(det));
        continue;
      }
    }

    const int top = std::max(0, static_cast<int>(std::min(c.y1 - 1.125f * c.h1, c.y2 - 1.125f * c.h2)));
    const int bottom = std::min(h_img, static_cast<int>(std::max(c.y1 + 1.125f * c.h1, c.y2 + 1.125f * c.h2)));
    const int left = std::max(0, static_cast<int>(c.x1));
    const int right = std::min(w_img, static_cast<int>(c.x2));
    if (bottom <= top + 2 || right <= left + 2) {
      continue;
    }

    auto [name, cls_conf] = classify(bgr(cv::Rect(left, top, right - left, bottom - top)));
    float conf_thr = 0.62f;
    if (is_numbered_vehicle(name)) {
      conf_thr = 0.85f;
    } else if (prefer_outpost && name == "outpost") {
      conf_thr = 0.42f;
    }
    if (name == "not_armor" || cls_conf < conf_thr) {
      continue;
    }

    ArmorDetection det;
    det.color = color;
    det.name = name;
    det.armor_type = (name == "one" || name == "base") ? "big" : "small";
    det.confidence = cls_conf;
    det.yolo_score = cls_conf;
    det.box = box;
    det.keypoints = kps;
    det.center = cv::Point2f(cx_p, cy_p);
    det.bar_height_px = avg_h;
    out.push_back(std::move(det));
  }

  return out;
}

std::vector<ArmorDetection> SpVisionYoloV8Detector::detect(
  const cv::Mat & bgr,
  const std::string & target_color,
  bool prefer_outpost)
{
  if (bgr.empty()) {
    return {};
  }
  const int h0 = bgr.rows;
  const int w0 = bgr.cols;
  const float scale = std::min(416.0f / static_cast<float>(h0), 416.0f / static_cast<float>(w0));
  const int nw = std::max(1, static_cast<int>(w0 * scale));
  const int nh = std::max(1, static_cast<int>(h0 * scale));

  cv::Mat letterbox = cv::Mat::zeros(416, 416, CV_8UC3);
  cv::Mat resized;
  cv::resize(bgr, resized, cv::Size(nw, nh));
  resized.copyTo(letterbox(cv::Rect(0, 0, nw, nh)));

  cv::Mat out = run_yolo_inference(letterbox);  // (1, 14, 3549)
  std::vector<ArmorDetection> results;

  if (out.dims == 3 && out.size[1] >= 14 && out.size[2] > 0) {
    const int num_anchors = out.size[2];
    const float * data = out.ptr<float>(0);
    auto at = [data, num_anchors](int ch, int a) -> float {
      return data[ch * num_anchors + a];
    };

    std::vector<cv::Rect> boxes;
    std::vector<float> confs;
    std::vector<int> ids;
    std::vector<std::array<cv::Point2f, 4>> kps_list;

    for (int a = 0; a < num_anchors; ++a) {
      const float s_blue = at(4, a);
      const float s_red = at(5, a);
      const int cls_id = (s_red > s_blue) ? 1 : 0;
      const float score = (cls_id == 1) ? s_red : s_blue;
      if (score < score_threshold_) {
        continue;
      }
      const float cx = at(0, a);
      const float cy = at(1, a);
      const float bw = at(2, a);
      const float bh = at(3, a);
      const int left = static_cast<int>((cx - 0.5f * bw) / scale);
      const int top = static_cast<int>((cy - 0.5f * bh) / scale);
      boxes.emplace_back(left, top, static_cast<int>(bw / scale), static_cast<int>(bh / scale));
      confs.push_back(score);
      ids.push_back(cls_id);

      std::array<cv::Point2f, 4> raw_kps;
      for (int k = 0; k < 4; ++k) {
        raw_kps[k] = cv::Point2f(at(6 + 2 * k, a) / scale, at(6 + 2 * k + 1, a) / scale);
      }
      kps_list.push_back(sort_keypoints(raw_kps));
    }

    if (!boxes.empty()) {
      std::vector<int> keep_idxs;
      cv::dnn::NMSBoxes(boxes, confs, score_threshold_, nms_threshold_, keep_idxs);
      for (int idx : keep_idxs) {
        const std::string & det_color = kArmorColors[ids[idx]];
        if (!target_color.empty() && det_color != target_color) {
          continue;
        }
        const auto & kps = kps_list[idx];
        cv::Mat pat = crop_armor_pattern(bgr, kps);
        if (pat.empty()) {
          continue;
        }
        auto [name, cls_conf] = classify(pat);
        float min_c = min_classify_conf_;
        if (is_numbered_vehicle(name)) {
          min_c = 0.85f;
        } else if (prefer_outpost && name == "outpost") {
          min_c = 0.42f;
        }
        if (name == "not_armor" || cls_conf < min_c) {
          continue;
        }
        const cv::Point2f center(
          0.25f * (kps[0].x + kps[1].x + kps[2].x + kps[3].x),
          0.25f * (kps[0].y + kps[1].y + kps[2].y + kps[3].y));
        const float h_left = static_cast<float>(cv::norm(kps[3] - kps[0]));
        const float h_right = static_cast<float>(cv::norm(kps[2] - kps[1]));

        ArmorDetection det;
        det.color = det_color;
        det.name = name;
        det.armor_type = (name == "one" || name == "base") ? "big" : "small";
        det.confidence = cls_conf;
        det.yolo_score = confs[idx];
        det.box = boxes[idx];
        det.keypoints = kps;
        det.center = center;
        det.bar_height_px = 0.5f * (h_left + h_right);
        results.push_back(std::move(det));
      }
    }
  }

  // Fuse full-res 1280x720 HSV lightbar + outpost sticker detections for long-range & oblique armors
  std::vector<std::string> colors_to_scan;
  if (target_color == "red" || target_color == "blue") {
    colors_to_scan.push_back(target_color);
  } else {
    colors_to_scan.emplace_back("red");
    colors_to_scan.emplace_back("blue");
  }

  for (const auto & col : colors_to_scan) {
    auto hsv_dets = detect_hsv_lightbars(bgr, col, prefer_outpost);
    for (auto & hdet : hsv_dets) {
      const float dup_thresh = std::max(24.0f, hdet.bar_height_px * 2.0f);
      bool dup = false;
      for (const auto & r : results) {
        if (r.color == hdet.color &&
            std::abs(r.center.x - hdet.center.x) < dup_thresh &&
            std::abs(r.center.y - hdet.center.y) < dup_thresh)
        {
          dup = true;
          break;
        }
      }
      if (!dup) {
        results.push_back(std::move(hdet));
      }
    }
  }

  return results;
}

}  // namespace sp_vision_25
