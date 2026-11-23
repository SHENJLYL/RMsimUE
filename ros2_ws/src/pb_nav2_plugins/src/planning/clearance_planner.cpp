#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <mutex>
#include <utility>
#include <vector>

#include "geometry_msgs/msg/point.hpp"
#include "nav2_core/global_planner.hpp"
#include "nav2_core/exceptions.hpp"
#include "nav2_theta_star_planner/theta_star.hpp"
#include "nav2_costmap_2d/cost_values.hpp"
#include "pluginlib/class_list_macros.hpp"
#include "pluginlib/class_loader.hpp"

namespace pb_nav2_plugins
{
namespace
{
struct Point
{
  double x{}, y{};
  Point operator+(Point other) const {return {x + other.x, y + other.y};}
  Point operator-(Point other) const {return {x - other.x, y - other.y};}
  Point operator*(double scale) const {return {x * scale, y * scale};}
  double length() const {return std::hypot(x, y);}
};

Point pointOf(const geometry_msgs::msg::PoseStamped & pose)
{
  return {pose.pose.position.x, pose.pose.position.y};
}

// Squared Euclidean distance to occupied cell centers via two separable passes.
void distanceLine(
  const std::vector<double> & input, int n, std::vector<double> & output,
  std::vector<int> & sites, std::vector<double> & edges)
{
  int k = 0;
  sites[0] = 0;
  edges[0] = -std::numeric_limits<double>::infinity();
  edges[1] = std::numeric_limits<double>::infinity();
  for (int i = 1; i < n; ++i) {
    const auto intersection = [&](int site) {
        return (input[i] + double(i) * i - input[site] - double(site) * site) / (2.0 * (i - site));
      };
    double boundary = intersection(sites[k]);
    while (boundary <= edges[k]) {boundary = intersection(sites[--k]);}
    sites[++k] = i;
    edges[k] = boundary;
    edges[k + 1] = std::numeric_limits<double>::infinity();
  }
  k = 0;
  for (int i = 0; i < n; ++i) {
    while (edges[k + 1] < i) {++k;}
    const double d = i - sites[k];
    output[i] = d * d + input[sites[k]];
  }
}

class ClearanceGrid
{
public:
  explicit ClearanceGrid(nav2_costmap_2d::Costmap2D & grid)
  {
    {
      std::unique_lock<nav2_costmap_2d::Costmap2D::mutex_t> lock(*grid.getMutex());
      width_ = static_cast<int>(grid.getSizeInCellsX());
      height_ = static_cast<int>(grid.getSizeInCellsY());
      origin_ = {grid.getOriginX(), grid.getOriginY()};
      resolution_ = grid.getResolution();
      distances_.resize(static_cast<size_t>(width_) * height_);
      const auto * cost = grid.getCharMap();
      costs_.assign(cost,cost+distances_.size());
      for (size_t i = 0; i < distances_.size(); ++i) {
        distances_[i] = cost[i] >= nav2_costmap_2d::LETHAL_OBSTACLE ? 0.0 : 1e10;
      }
    }
    if (width_ < 2 || height_ < 2) {return;}
    const int n = std::max(width_, height_);
    std::vector<double> input(n), output(n), edges(n + 1);
    std::vector<int> sites(n);
    for (int y = 0; y < height_; ++y) {
      const size_t offset = static_cast<size_t>(y) * width_;
      std::copy_n(distances_.begin() + offset, width_, input.begin());
      distanceLine(input, width_, output, sites, edges);
      std::copy_n(output.begin(), width_, distances_.begin() + offset);
    }
    for (int x = 0; x < width_; ++x) {
      for (int y = 0; y < height_; ++y) {input[y] = distances_[index(x, y)];}
      distanceLine(input, height_, output, sites, edges);
      for (int y = 0; y < height_; ++y) {distances_[index(x, y)] = std::sqrt(output[y]) * resolution_;}
    }
  }

  double at(Point p) const
  {
    const Point cell = (p - origin_) * (1.0 / resolution_) - Point{0.5, 0.5};
    if (!std::isfinite(cell.x) || !std::isfinite(cell.y) ||
      cell.x < 0.0 || cell.y < 0.0 || cell.x >= width_ - 1 || cell.y >= height_ - 1)
    {return 0.0;}
    const int x = static_cast<int>(cell.x), y = static_cast<int>(cell.y);
    const double u = cell.x - x, v = cell.y - y;
    const auto d = [&](int i, int j) {return distances_[index(i, j)];};
    const double value = (1.0-v)*((1.0-u)*d(x,y)+u*d(x+1,y)) +
      v*((1.0-u)*d(x,y+1)+u*d(x+1,y+1));
    const double boundary = resolution_ * std::min(
      std::min(cell.x+0.5, cell.y+0.5), std::min(width_-cell.x-0.5, height_-cell.y-0.5));
    return std::max(0.0, std::min(value - 0.72 * resolution_, boundary));
  }

  template<typename Iterator, typename Position>
  bool clearRoute(Point start, Iterator first, Iterator last, Position position) const
  {
    constexpr double minimum = 0.41;
    constexpr double start_minimum = 0.22;
    if (first == last || !(at(start) >= start_minimum)) {return false;}
    double traversed = 0.0;
    for (; first != last; ++first) {
      const Point end = position(*first);
      const auto delta = end-start;
      const double length = delta.length();
      if (!std::isfinite(length)) {return false;}
      const int steps = std::max(1, static_cast<int>(std::ceil(length/(resolution_*.5))));
      for (int i=1; i<=steps; ++i) {
        const double frac = double(i)/steps;
        const double d_here = traversed + length * frac;
        const double req = (d_here < 1.00) ? std::min(minimum, start_minimum + (minimum - start_minimum) * (d_here / 1.00)) : minimum;
        if (!(at(start+delta*frac) >= req)) {return false;}
      }
      traversed += length;
      start = end;
    }
    return true;
  }

  nav_msgs::msg::Path clearanceSeed(
    const geometry_msgs::msg::PoseStamped & start, const geometry_msgs::msg::PoseStamped & goal,
    double desired, double minimum) const
  {
    // Repair an invalid raw route on a private snapshot. Costs here express
    // the same preferred center clearance as the spline optimizer.
    nav2_costmap_2d::Costmap2D seed(width_, height_, resolution_, origin_.x, origin_.y, 0);
    auto * cells = seed.getCharMap();
    for (int y = 0; y < height_; ++y) {
      for (int x = 0; x < width_; ++x) {
        const Point center = origin_ + Point{(x+.5)*resolution_, (y+.5)*resolution_};
        const double clearance = at(center);
        const size_t i = index(x, y);
        if (clearance < minimum) {
          cells[i] = nav2_costmap_2d::LETHAL_OBSTACLE;
        } else {
          const double shortfall = std::clamp((desired-clearance)/(desired-minimum), 0.0, 1.0);
          cells[i] = std::max(costs_[i], static_cast<unsigned char>(200.0*shortfall));
        }
      }
    }
    unsigned int x,y;
    nav_msgs::msg::Path result;
    result.header = goal.header;
    if (!seed.worldToMap(start.pose.position.x,start.pose.position.y,x,y) ||
      !seed.worldToMap(goal.pose.position.x,goal.pose.position.y,x,y)) {return result;}
    theta_star::ThetaStar search;
    search.costmap_ = &seed;
    search.how_many_corners_ = 8;
    search.allow_unknown_ = false;
    search.w_euc_cost_ = search.w_heuristic_cost_ = 1.0;
    search.w_traversal_cost_ = 10.0;
    search.setStartAndGoal(start, goal);
    std::vector<coordsW> points;
    if (search.isUnsafeToPlan() || !search.generatePath(points)) {return result;}
    result.poses.push_back(start);
    for (const auto & point : points) {
      auto p = start;
      p.pose.position.x = point.x;
      p.pose.position.y = point.y;
      if ((pointOf(p)-pointOf(result.poses.back())).length() > 1.e-6) {result.poses.push_back(p);}
    }
    if ((pointOf(goal)-pointOf(result.poses.back())).length() > 1.e-6) {result.poses.push_back(goal);}
    else {result.poses.back() = goal;}
    return result;
  }

  Point gradientAt(Point p) const
  {
    const double r = 0.5 * resolution_;
    return Point{at(p + Point{r,0.0}) - at(p - Point{r,0.0}),
      at(p + Point{0.0,r}) - at(p - Point{0.0,r})} * (0.5 / r);
  }

private:
  size_t index(int x, int y) const {return static_cast<size_t>(y) * width_ + x;}
  int width_{}, height_{};
  Point origin_;
  double resolution_{};
  std::vector<double> distances_;
  std::vector<unsigned char> costs_;
};

std::vector<Point> resample(const nav_msgs::msg::Path & path, double resolution)
{
  if (path.poses.size() < 2) {return {};}
  std::vector<double> distances{0.0};
  for (size_t i = 1; i < path.poses.size(); ++i) {
    distances.push_back(distances.back() + (pointOf(path.poses[i]) - pointOf(path.poses[i-1])).length());
  }
  const double length = distances.back();
  if (!std::isfinite(length) || length < 0.65 || length > 300.0) {return {};}
  const size_t count = std::max<size_t>(2, static_cast<size_t>(std::ceil(length / resolution)));
  std::vector<Point> points{pointOf(path.poses.front())};
  for (size_t i = 1, j = 1; i < count; ++i) {
    const double at = length * i / count;
    while (j + 1 < distances.size() && distances[j] <= at) {++j;}
    const double fraction = (at - distances[j - 1]) / (distances[j] - distances[j - 1]);
    const auto a = pointOf(path.poses[j - 1]), b = pointOf(path.poses[j]);
    points.push_back(a + (b-a)*fraction);
  }
  points.push_back(pointOf(path.poses.back()));
  return points;
}

double routeCost(
  const ClearanceGrid & grid, const std::vector<Point> & points, const std::vector<Point> & anchor,
  double margin, double escape, std::vector<Point> & gradient)
{
  std::fill(gradient.begin(), gradient.end(), Point{});
  constexpr double bend_weight = 8.0;
  double total = 0.0;
  for (size_t i = 1; i < points.size(); ++i) {
    const Point a = points[i - 1], b = points[i], span = b-a;
    const double length = span.length();
    const auto along = span * (1.0 / std::max(length, 1e-6));
    total += length;
    gradient[i-1] = gradient[i-1] - along;
    gradient[i] = gradient[i] + along;
    for (double ratio : {0.2, 0.4, 0.6, 0.8, 1.0}) {
      const Point at = a + span * ratio;
      const double missing = margin - grid.at(at);
      if (missing <= 0.0) {continue;}
      total += 110.0 * missing * missing;
      const Point fix = grid.gradientAt(at) * (-220.0 * missing);
      gradient[i-1] = gradient[i-1] + fix * (1.0-ratio);
      gradient[i] = gradient[i] + fix * ratio;
    }
    if (escape > 0 && i + 3 < points.size() && length > 1e-6) {
      // Give the vehicle room to brake along the incoming tangent at a bend.
      // The controller still checks every measured and requested swept stop.
      for (double ahead : {0.5 * escape, escape}) {
        const auto stop = b + along * ahead;
        const double missing = std::min(margin, 0.70) - grid.at(stop);
        if (missing <= 0.0) {continue;}
        constexpr double weight = 36.0;
        total += weight * missing * missing;
        const auto fix = grid.gradientAt(stop) * (-2.0 * weight * missing);
        const auto parallel = along * (along.x * fix.x + along.y * fix.y);
        const auto rotation = (fix - parallel) * (ahead / length);
        gradient[i-1] = gradient[i-1] - rotation;
        gradient[i] = gradient[i] + fix + rotation;
      }
    }
    if (i + 1 >= points.size()) {continue;}
    const Point bend = a - b * 2.0 + points[i+1];
    const Point restore = b - anchor[i];
    total += bend_weight * bend.length() * bend.length() + 0.02 * restore.length() * restore.length();
    gradient[i-1] = gradient[i-1] + bend * (2.0 * bend_weight);
    gradient[i] = gradient[i] - bend * (4.0 * bend_weight) + restore * 0.04;
    gradient[i+1] = gradient[i+1] + bend * (2.0 * bend_weight);
  }
  return total;
}

std::vector<Point> optimize(const ClearanceGrid & grid, const std::vector<Point> & anchor, double margin, double escape)
{
  auto points = anchor, trial = anchor;
  std::vector<Point> gradient(points.size()), trial_gradient(points.size());
  double cost = routeCost(grid, points, anchor, margin, escape, gradient);
  double step = 0.027;
  for (int pass = 0; pass < 130 && step >= 5e-5; ++pass) {
    for (size_t i = 1; i + 1 < points.size(); ++i) {
      const Point move = gradient[i] * (-std::min(step, 0.10 / std::max(gradient[i].length(), 1e-7)));
      const Point offset = points[i] + move - anchor[i];
      trial[i] = anchor[i] + offset * std::min(1.0, 1.45 / std::max(offset.length(), 1e-7));
    }
    const double candidate = routeCost(grid, trial, anchor, margin, escape, trial_gradient);
    if (candidate < cost) {
      points.swap(trial);
      gradient.swap(trial_gradient);
      if (cost - candidate < 1e-5) {break;}
      cost = candidate;
      step = std::min(0.05, 1.16 * step);
    } else {
      step *= 0.50;
    }
  }
  return points;
}

std::vector<Point> interpolate(const std::vector<Point> & nodes)
{
  std::vector<Point> dense{nodes.front()};
  for (size_t i = 1; i < nodes.size(); ++i) {
    const auto a = nodes[i-1], b = nodes[i];
    const auto tangent_a = (b - nodes[i < 2 ? i-1 : i-2]) * (i < 2 ? 1.0 : 0.5);
    const auto tangent_b = (nodes[i+1 < nodes.size() ? i+1 : i] - a) *
      (i+1 < nodes.size() ? 0.5 : 1.0);
    const int count = std::max(1, static_cast<int>(std::ceil((b-a).length() / 0.042)));
    for (int j = 1; j <= count; ++j) {
      const double t = double(j) / count;
      dense.push_back(a * (2*t*t*t - 3*t*t + 1) + tangent_a * (t*t*t - 2*t*t + t) +
        b * (-2*t*t*t + 3*t*t) + tangent_b * (t*t*t - t*t));
    }
  }
  return dense;
}
}  // namespace

class ClearancePlanner : public nav2_core::GlobalPlanner
{
public:
  void configure(
    const rclcpp_lifecycle::LifecycleNode::WeakPtr & parent, std::string name,
    std::shared_ptr<tf2_ros::Buffer> tf, std::shared_ptr<nav2_costmap_2d::Costmap2DROS> map) override
  {
    const auto node = parent.lock();
    logger_ = node->get_logger();
    clock_ = node->get_clock();
    costmap_ = map;
    const auto backend = node->declare_parameter<std::string>(
      name + ".route_backend", "nav2_theta_star_planner/ThetaStarPlanner");
    margin_ = node->declare_parameter<double>(name + ".curve_clearance", 0.64);
    escape_ = node->declare_parameter<double>(name + ".curve_stopping_tangent", 0.0);
    if (!std::isfinite(escape_) || escape_ < 0.0 || escape_ > 1.5) {
      throw std::invalid_argument("Invalid path stopping tangent");
    }
    if (!std::isfinite(margin_) || margin_ < 0.53 || margin_ > 1.5) {
      throw std::invalid_argument("Invalid curve clearance");
    }
    delegate_ = loader_.createSharedInstance(backend);
    delegate_->configure(parent, std::move(name), std::move(tf), std::move(map));
  }

  void activate() override {delegate_->activate();}
  void deactivate() override {delegate_->deactivate(); cached_.poses.clear();}
  void cleanup() override {delegate_->cleanup(); cached_.poses.clear(); delegate_.reset(); costmap_.reset();}

  nav_msgs::msg::Path createPlan(
    const geometry_msgs::msg::PoseStamped & start, const geometry_msgs::msg::PoseStamped & goal) override
  {
    ClearanceGrid grid(*costmap_->getCostmap());
    if (!cached_.poses.empty() && cached_.header.frame_id == goal.header.frame_id &&
      (cached_goal_ - pointOf(goal)).length() < 1e-5)
    {
      const auto limit = std::min(cached_.poses.size(), progress_ + 140);
      auto candidate = progress_;
      double distance = std::numeric_limits<double>::infinity();
      for (size_t i = progress_; i < limit; ++i) {
        const double current = (pointOf(cached_.poses[i]) - pointOf(start)).length();
        if (current < distance) {distance = current; candidate = i;}
      }
      if (distance <= 0.90 && grid.clearRoute(pointOf(start),cached_.poses.begin()+candidate,
          cached_.poses.end(),[](const auto & pose){return pointOf(pose);}))
      {
        progress_ = candidate;
        nav_msgs::msg::Path path;
        path.header = cached_.header;
        path.header.stamp = start.header.stamp;
        path.poses.assign(cached_.poses.begin() + candidate, cached_.poses.end());
        path.poses.back().pose.orientation = goal.pose.orientation;
        return path;
      }
    }
    auto raw = delegate_->createPlan(start, goal);
    if (raw.poses.empty()) {throw nav2_core::PlannerException("No route returned by route planner");}
    std::vector<Point> curve;
    // Recompute the whole route at finer spacing if the first smoothed route
    // is blocked. Short plans and the connection from the current pose use
    // exactly the same segment clearance check as long plans.
    const auto validated_curve = [&](const nav_msgs::msg::Path & seed,double spacing) {
      std::vector<Point> empty;
      if (seed.poses.empty()) {return empty;}
      auto anchors=resample(seed,spacing);
      if (anchors.empty()) {
        for (const auto & pose : seed.poses) {
          const auto p=pointOf(pose);
          if (anchors.empty() || (p-anchors.back()).length()>1.e-8) {anchors.push_back(p);}
        }
      }
      anchors.front()=pointOf(start);
      anchors.back()=pointOf(goal);
      auto smooth=anchors.size()==1 ? anchors : interpolate(optimize(grid,anchors,margin_,escape_));
      return grid.clearRoute(pointOf(start),smooth.begin(),smooth.end(),[](Point p){return p;}) ? smooth : empty;
    };
    curve=validated_curve(raw,.60);
    if (curve.empty() && grid.at(pointOf(start))>=.38 && grid.at(pointOf(goal))>=.38) {
      // Replan globally on a private snapshot which excludes cells without
      // physical clearance. Smooth and verify the entire result again.
      const auto second=grid.clearanceSeed(start,goal,margin_,.42);
      curve=validated_curve(second,.32);
      if (!curve.empty()) {RCLCPP_INFO(logger_, "Validated complete replacement route");}
    }
    if (curve.empty()) {
      // If we still have a valid cached route for the same goal, reuse it instead of aborting into BackUp!
      if (!cached_.poses.empty() && cached_.header.frame_id == goal.header.frame_id &&
        (cached_goal_ - pointOf(goal)).length() < 1e-5)
      {
        const auto limit = std::min(cached_.poses.size(), progress_ + 160);
        auto candidate = progress_;
        double distance = std::numeric_limits<double>::infinity();
        for (size_t i = progress_; i < limit; ++i) {
          const double current = (pointOf(cached_.poses[i]) - pointOf(start)).length();
          if (current < distance) {distance = current; candidate = i;}
        }
        if (distance <= 1.20) {
          progress_ = candidate;
          nav_msgs::msg::Path path;
          path.header = cached_.header;
          path.header.stamp = start.header.stamp;
          path.poses.assign(cached_.poses.begin() + candidate, cached_.poses.end());
          path.poses.back().pose.orientation = goal.pose.orientation;
          RCLCPP_WARN_THROTTLE(logger_,*clock_,1000,"Reusing cached route (dist=%.2f, start clearance=%.3f)",
            distance, grid.at(pointOf(start)));
          return path;
        }
      }
      cached_.poses.clear();
      RCLCPP_WARN_THROTTLE(logger_,*clock_,1000,"Rejected full route: start clearance %.3f, goal %.3f",
        grid.at(pointOf(start)),grid.at(pointOf(goal)));
      throw nav2_core::PlannerException("Replanned route does not satisfy clearance");
    }
    nav_msgs::msg::Path path;
    path.header = raw.header;
    path.poses.reserve(curve.size());
    for (const auto & p : curve) {
      auto pose = raw.poses.front();
      pose.pose.position.x = p.x;
      pose.pose.position.y = p.y;
      path.poses.push_back(std::move(pose));
    }
    path.poses.back().pose.orientation = raw.poses.back().pose.orientation;
    cached_ = path;
    cached_goal_ = pointOf(goal);
    progress_ = 0;
    RCLCPP_INFO(logger_, "Validated a continuous clearance path with %zu samples", path.poses.size());
    return path;
  }

private:
  pluginlib::ClassLoader<nav2_core::GlobalPlanner> loader_{"nav2_core", "nav2_core::GlobalPlanner"};
  std::shared_ptr<nav2_core::GlobalPlanner> delegate_;
  std::shared_ptr<nav2_costmap_2d::Costmap2DROS> costmap_;
  nav_msgs::msg::Path cached_;
  Point cached_goal_;
  size_t progress_{};
  double margin_{};
  double escape_{};
  rclcpp::Clock::SharedPtr clock_;
  rclcpp::Logger logger_{rclcpp::get_logger("clearance_planner")};
};
}  // namespace pb_nav2_plugins

PLUGINLIB_EXPORT_CLASS(pb_nav2_plugins::ClearancePlanner, nav2_core::GlobalPlanner)
