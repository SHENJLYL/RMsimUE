#include "li_initialization.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <limits>

namespace
{
// Observe actual subscription delivery without changing messages, timestamps,
// acceptance, QoS, queue operations, or filter state. No new callback threads.
struct ImuReceiptDiagnostics
{
  using Clock = std::chrono::steady_clock;
  std::uint64_t received = 0, accepted = 0, duplicates = 0, backwards = 0;
  std::uint64_t gaps = 0, inferred_missing = 0;
  double first_stamp = 0.0, previous_stamp = 0.0;
  double min_positive_dt = std::numeric_limits<double>::infinity();
  double max_header_dt = 0.0, max_wall_gap = 0.0;
  double window_max_header_dt = 0.0, window_max_wall_gap = 0.0;
  std::size_t queue_peak = 0;
  Clock::time_point first_wall, previous_wall, last_log;

  void observe(double raw_stamp, double adjusted_stamp, bool was_accepted, std::size_t queue_size)
  {
    const auto now = Clock::now();
    if (received == 0) {
      first_stamp = raw_stamp;
      first_wall = previous_wall = last_log = now;
    } else {
      const double dt = raw_stamp - previous_stamp;
      const double wall_gap = std::chrono::duration<double>(now - previous_wall).count();
      max_wall_gap = std::max(max_wall_gap, wall_gap);
      window_max_wall_gap = std::max(window_max_wall_gap, wall_gap);
      if (dt < 0.0) ++backwards;
      else if (dt == 0.0) ++duplicates;
      else {
        min_positive_dt = std::min(min_positive_dt, dt);
        max_header_dt = std::max(max_header_dt, dt);
        window_max_header_dt = std::max(window_max_header_dt, dt);
        if (std::isfinite(imu_time_inte) && imu_time_inte > 0.0 && dt > 1.5 * imu_time_inte) {
          ++gaps;
          inferred_missing += static_cast<std::uint64_t>(
            std::max(0.0, std::floor(dt / imu_time_inte + 0.5) - 1.0));
        }
      }
    }
    ++received;
    if (was_accepted) ++accepted;
    queue_peak = std::max(queue_peak, queue_size);
    previous_stamp = raw_stamp;
    previous_wall = now;
    if (std::chrono::duration<double>(now - last_log).count() < 5.0) return;
    RCLCPP_INFO(
      rclcpp::get_logger("li_initialization"),
      "POINTLIO_IMU_RX received=%llu accepted=%llu duplicates=%llu backwards=%llu "
      "header_gaps=%llu inferred_missing=%llu nominal_period_s=%.9f "
      "first_stamp_s=%.9f raw_stamp_s=%.9f adjusted_stamp_s=%.9f "
      "min_header_dt_s=%.9f max_header_dt_s=%.9f max_wall_gap_s=%.6f "
      "window_max_header_dt_s=%.9f window_max_wall_gap_s=%.6f "
      "queue_size=%llu queue_peak=%llu wall_elapsed_s=%.6f",
      static_cast<unsigned long long>(received), static_cast<unsigned long long>(accepted),
      static_cast<unsigned long long>(duplicates), static_cast<unsigned long long>(backwards),
      static_cast<unsigned long long>(gaps), static_cast<unsigned long long>(inferred_missing),
      imu_time_inte, first_stamp, raw_stamp, adjusted_stamp,
      std::isfinite(min_positive_dt) ? min_positive_dt : 0.0, max_header_dt, max_wall_gap,
      window_max_header_dt, window_max_wall_gap,
      static_cast<unsigned long long>(queue_size), static_cast<unsigned long long>(queue_peak),
      std::chrono::duration<double>(now - first_wall).count());
    last_log = now;
    window_max_header_dt = window_max_wall_gap = 0.0;
  }
};
}  // namespace

bool data_accum_finished = false, data_accum_start = false, online_calib_finish = false,
     refine_print = false;
int frame_num_init = 0;
double time_lag_IMU_wtr_lidar = 0.0, move_start_time = 0.0,
       online_calib_starts_time = 0.0;  //, mean_acc_norm = 9.81;
double imu_first_time = 0.0;
bool lose_lid = false;
double timediff_imu_wrt_lidar = 0.0;
bool timediff_set_flg = false;
V3D gravity_lio = V3D::Zero();
mutex mtx_buffer;
sensor_msgs::msg::Imu imu_last, imu_next;
// sensor_msgs::msg::Imu::ConstSharedPtr imu_last_ptr;
PointCloudXYZI::Ptr ptr_con(new PointCloudXYZI());
double T1[MAXN], s_plot[MAXN], s_plot2[MAXN], s_plot3[MAXN], s_plot11[MAXN];

condition_variable sig_buffer;
int scan_count = 0;
int frame_ct = 0, wait_num = 0;
std::mutex m_time;
bool lidar_pushed = false, imu_pushed = false;
std::deque<PointCloudXYZI::Ptr> lidar_buffer;
std::deque<double> time_buffer;
std::deque<sensor_msgs::msg::Imu::ConstSharedPtr> imu_deque;

void standard_pcl_cbk(const sensor_msgs::msg::PointCloud2::SharedPtr & msg)
{
  // mtx_buffer.lock();
  scan_count++;
  double preprocess_start_time = omp_get_wtime();
  if (rclcpp::Time(msg->header.stamp).seconds() < last_timestamp_lidar) {
    RCLCPP_ERROR(rclcpp::get_logger("li_initialization"), "lidar loop back, clear buffer");
    return;
  }

  last_timestamp_lidar = rclcpp::Time(msg->header.stamp).seconds();
  // printf("check lidar time %f\n", last_timestamp_lidar);
  // if (abs(last_timestamp_imu - last_timestamp_lidar) > 1.0 && !timediff_set_flg && !imu_deque.empty()) {
  //     timediff_set_flg = true;
  //     timediff_imu_wrt_lidar = last_timestamp_imu - last_timestamp_lidar;
  //     printf("Self sync IMU and LiDAR, HARD time lag is %.10lf \n \n", timediff_imu_wrt_lidar);
  // }

  if ((lidar_type == VELO16 || lidar_type == OUST64 || lidar_type == HESAIxt32) && cut_frame_init) {
    deque<PointCloudXYZI::Ptr> ptr;
    deque<double> timestamp_lidar;
    p_pre->process_cut_frame_pcl2(msg, ptr, timestamp_lidar, cut_frame_num, scan_count);
    while (!ptr.empty() && !timestamp_lidar.empty()) {
      lidar_buffer.push_back(ptr.front());
      ptr.pop_front();
      time_buffer.push_back(timestamp_lidar.front() / double(1000));  //unit:s
      timestamp_lidar.pop_front();
    }
  } else {
    PointCloudXYZI::Ptr ptr(new PointCloudXYZI(20000, 1));
    p_pre->process(msg, ptr);
    if (con_frame) {
      if (frame_ct == 0) {
        time_con = last_timestamp_lidar;  //msg->header.stamp.toSec();
      }
      if (frame_ct < 10) {
        for (int i = 0; i < ptr->size(); i++) {
          ptr->points[i].curvature += (last_timestamp_lidar - time_con) * 1000;
          ptr_con->push_back(ptr->points[i]);
        }
        frame_ct++;
      } else {
        PointCloudXYZI::Ptr ptr_con_i(new PointCloudXYZI(10000, 1));
        // std::cout << "ptr div num:" << ptr_div->size() << '\n';
        *ptr_con_i = *ptr_con;
        lidar_buffer.push_back(ptr_con_i);
        double time_con_i = time_con;
        time_buffer.push_back(time_con_i);
        ptr_con->clear();
        frame_ct = 0;
      }
    } else {
      if (!ptr->points.empty()) {
        lidar_buffer.emplace_back(ptr);
        time_buffer.emplace_back(rclcpp::Time(msg->header.stamp).seconds());
      }
    }
  }
  s_plot11[scan_count] = omp_get_wtime() - preprocess_start_time;
  // mtx_buffer.unlock();
  // sig_buffer.notify_all();
}

void livox_pcl_cbk(const livox_ros_driver2::msg::CustomMsg::SharedPtr & msg)
{
  // mtx_buffer.lock();
  double preprocess_start_time = omp_get_wtime();
  scan_count++;
  if (rclcpp::Time(msg->header.stamp).seconds() < last_timestamp_lidar) {
    RCLCPP_ERROR(rclcpp::get_logger("li_initialization"), "lidar loop back, clear buffer");
    return;
  }

  last_timestamp_lidar = rclcpp::Time(msg->header.stamp).seconds();
  // if (abs(last_timestamp_imu - last_timestamp_lidar) > 1.0 && !timediff_set_flg && !imu_deque.empty()) {
  //     timediff_set_flg = true;
  //     timediff_imu_wrt_lidar = last_timestamp_imu - last_timestamp_lidar;
  //     printf("Self sync IMU and LiDAR, HARD time lag is %.10lf \n \n", timediff_imu_wrt_lidar);
  // }

  if (cut_frame_init) {
    deque<PointCloudXYZI::Ptr> ptr;
    deque<double> timestamp_lidar;
    p_pre->process_cut_frame_livox(msg, ptr, timestamp_lidar, cut_frame_num, scan_count);

    while (!ptr.empty() && !timestamp_lidar.empty()) {
      lidar_buffer.push_back(ptr.front());
      ptr.pop_front();
      time_buffer.push_back(timestamp_lidar.front() / double(1000));  //unit:s
      timestamp_lidar.pop_front();
    }
  } else {
    PointCloudXYZI::Ptr ptr(new PointCloudXYZI(10000, 1));
    p_pre->process(msg, ptr);
    if (con_frame) {
      if (frame_ct == 0) {
        time_con = last_timestamp_lidar;  //msg->header.stamp.toSec();
      }
      if (frame_ct < 10) {
        for (int i = 0; i < ptr->size(); i++) {
          ptr->points[i].curvature += (last_timestamp_lidar - time_con) * 1000;
          ptr_con->push_back(ptr->points[i]);
        }
        frame_ct++;
      } else {
        PointCloudXYZI::Ptr ptr_con_i(new PointCloudXYZI(10000, 1));
        // std::cout << "ptr div num:" << ptr_div->size() << '\n';
        *ptr_con_i = *ptr_con;
        double time_con_i = time_con;
        lidar_buffer.push_back(ptr_con_i);
        time_buffer.push_back(time_con_i);
        ptr_con->clear();
        frame_ct = 0;
      }
    } else {
      if (!ptr->points.empty()) {
        lidar_buffer.emplace_back(ptr);
        time_buffer.emplace_back(rclcpp::Time(msg->header.stamp).seconds());
      }
    }
  }
  s_plot11[scan_count] = omp_get_wtime() - preprocess_start_time;
  // mtx_buffer.unlock();
  // sig_buffer.notify_all();
}

void imu_cbk(const sensor_msgs::msg::Imu::ConstSharedPtr & msg_in)
{
  static ImuReceiptDiagnostics receipt_diagnostics;
  const double raw_stamp = get_time_sec(msg_in->header.stamp);
  // mtx_buffer.lock();

  sensor_msgs::msg::Imu::SharedPtr msg(new sensor_msgs::msg::Imu(*msg_in));
  // publish_count ++;

  msg->header.stamp = get_ros_time(
    get_time_sec(msg_in->header.stamp) - timediff_imu_wrt_lidar - time_lag_IMU_wtr_lidar);

  double timestamp = get_time_sec(msg->header.stamp);
  // printf("time_diff%f, %f, %f\n", last_timestamp_imu - timestamp, last_timestamp_imu, timestamp);

  if (timestamp < last_timestamp_imu) {
    receipt_diagnostics.observe(raw_stamp, timestamp, false, imu_deque.size());
    RCLCPP_ERROR(rclcpp::get_logger("li_initialization"), "imu loop back, clear deque");
    // imu_deque.shrink_to_fit();
    // std::cout << "check time:" << timestamp << ";" << last_timestamp_imu << '\n';
    // printf("time_diff%f, %f, %f\n", last_timestamp_imu - timestamp, last_timestamp_imu, timestamp);

    // mtx_buffer.unlock();
    // sig_buffer.notify_all();
    return;
  }
  imu_deque.emplace_back(msg);
  last_timestamp_imu = timestamp;
  receipt_diagnostics.observe(raw_stamp, timestamp, true, imu_deque.size());
  // mtx_buffer.unlock();
  // sig_buffer.notify_all();
}

bool sync_packages(MeasureGroup & meas)
{
  {
    if (!imu_en) {
      if (!lidar_buffer.empty()) {
        if (!lidar_pushed) {
          meas.lidar = lidar_buffer.front();
          meas.lidar_beg_time = time_buffer.front();
          lose_lid = false;
          if (meas.lidar->points.empty()) {
            std::cout << "lose lidar" << '\n';
            // return false;
            lose_lid = true;
          } else {
            double end_time = meas.lidar->points.back().curvature;
            for (auto pt : meas.lidar->points) {
              if (pt.curvature > end_time) {
                end_time = pt.curvature;
              }
            }
            lidar_end_time = meas.lidar_beg_time + end_time / double(1000);
            meas.lidar_last_time = lidar_end_time;
          }
          lidar_pushed = true;
        }

        time_buffer.pop_front();
        lidar_buffer.pop_front();
        lidar_pushed = false;
        if (!lose_lid) {
          return true;
        } else {
          return false;
        }
      }
      return false;
    }

    if (lidar_buffer.empty() || imu_deque.empty()) {
      return false;
    }
    /*** push a lidar scan ***/
    if (!lidar_pushed) {
      lose_lid = false;
      meas.lidar = lidar_buffer.front();
      meas.lidar_beg_time = time_buffer.front();
      if (meas.lidar->points.size() < 1) {
        std::cout << "lose lidar" << '\n';
        lose_lid = true;
        // lidar_buffer.pop_front();
        // time_buffer.pop_front();
        // return false;
      } else {
        double end_time = meas.lidar->points.back().curvature;
        for (auto pt : meas.lidar->points) {
          if (pt.curvature > end_time) {
            end_time = pt.curvature;
          }
        }
        lidar_end_time = meas.lidar_beg_time + end_time / double(1000);
        // std::cout << "check time lidar:" << end_time << '\n';
        meas.lidar_last_time = lidar_end_time;
      }
      lidar_pushed = true;
    }

    if (!lose_lid && (last_timestamp_imu < lidar_end_time)) {
      return false;
    }
    if (lose_lid && last_timestamp_imu < meas.lidar_beg_time + lidar_time_inte) {
      return false;
    }

    if (!lose_lid && !imu_pushed) {
      /*** push imu data, and pop from imu buffer ***/
      if (p_imu->imu_need_init_) {
        double imu_time = get_time_sec(imu_deque.front()->header.stamp);
        imu_next = *(imu_deque.front());
        meas.imu.shrink_to_fit();
        while (imu_time < lidar_end_time) {
          meas.imu.emplace_back(imu_deque.front());
          imu_last = imu_next;
          imu_deque.pop_front();
          if (imu_deque.empty()) break;
          imu_time = get_time_sec(imu_deque.front()->header.stamp);  // can be changed
          imu_next = *(imu_deque.front());
        }
      }
      imu_pushed = true;
    }

    if (lose_lid && !imu_pushed) {
      /*** push imu data, and pop from imu buffer ***/
      if (p_imu->imu_need_init_) {
        double imu_time = get_time_sec(imu_deque.front()->header.stamp);
        meas.imu.shrink_to_fit();

        imu_next = *(imu_deque.front());
        while (imu_time < meas.lidar_beg_time + lidar_time_inte) {
          meas.imu.emplace_back(imu_deque.front());
          imu_last = imu_next;
          imu_deque.pop_front();
          if (imu_deque.empty()) break;
          imu_time = get_time_sec(imu_deque.front()->header.stamp);  // can be changed
          imu_next = *(imu_deque.front());
        }
      }
      imu_pushed = true;
    }

    lidar_buffer.pop_front();
    time_buffer.pop_front();
    lidar_pushed = false;
    imu_pushed = false;
    return true;
  }
}
