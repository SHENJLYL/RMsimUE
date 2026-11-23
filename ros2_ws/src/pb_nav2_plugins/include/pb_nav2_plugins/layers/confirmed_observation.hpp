#ifndef PB_NAV2_PLUGINS__LAYERS__CONFIRMED_OBSERVATION_HPP_
#define PB_NAV2_PLUGINS__LAYERS__CONFIRMED_OBSERVATION_HPP_

#include <algorithm>
#include <cstdint>
#include <vector>

namespace pb_nav2_costmap_2d
{
// Maintains two distinct local observations for an optional planning overlay.
// This state is separate from the original global and local obstacle layers.
class ConfirmedObservation
{
public:
  void clear()
  {
    stamp_ = 0;
    previous_.clear();
    confirmed_.clear();
  }

  std::int64_t stamp() const {return stamp_;}
  const std::vector<unsigned int> & marks() const {return confirmed_;}

  void update(
    std::int64_t stamp, std::int64_t max_gap, int width, int height, int radius,
    std::vector<unsigned int> & current)
  {
    if (stamp <= 0) {
      clear();
      return;
    }
    if (stamp == stamp_) {return;}
    std::sort(current.begin(), current.end());
    current.erase(std::unique(current.begin(), current.end()), current.end());
    confirmed_.clear();
    if (stamp > stamp_ && stamp - stamp_ <= max_gap) {
      for (const auto index : current) {
        const int x = static_cast<int>(index % width);
        const int y = static_cast<int>(index / width);
        bool hit = false;
        for (int dy = -radius; dy <= radius && !hit; ++dy) {
          if (y + dy < 0 || y + dy >= height) {continue;}
          for (int dx = -radius; dx <= radius && !hit; ++dx) {
            if (x + dx < 0 || x + dx >= width || dx * dx + dy * dy > radius * radius) {
              continue;
            }
            const auto measurement = static_cast<unsigned int>((y + dy) * width + x + dx);
            hit = std::binary_search(previous_.begin(), previous_.end(), measurement);
          }
        }
        if (hit) {confirmed_.push_back(index);}
      }
    }
    previous_.swap(current);
    stamp_ = stamp;
  }

private:
  std::int64_t stamp_{};
  std::vector<unsigned int> previous_, confirmed_;
};
}  // namespace pb_nav2_costmap_2d

#endif  // PB_NAV2_PLUGINS__LAYERS__CONFIRMED_OBSERVATION_HPP_
