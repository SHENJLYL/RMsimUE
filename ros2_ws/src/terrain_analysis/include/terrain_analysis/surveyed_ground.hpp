#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace terrain_analysis
{
// Surveyed support heights are in the map frame. Masked cells cover static
// obstacles and their edges. Height registration still requires current,
// observed low returns in the LiDAR cloud.
class SurveyedGround
{
public:
  explicit SurveyedGround(const std::string & file, bool align_xy = false)
  : align_xy_(align_xy)
  {
    std::ifstream in(file,std::ios::binary);
    std::string version;
    in >> version >> width_ >> height_ >> x0_ >> y0_ >> step_;
    if (!in || version!="RMSIM_SURVEY_V2" || width_<5 || width_>2048 || height_<5 || height_>2048 ||
      !std::isfinite(x0_) || !std::isfinite(y0_) || !std::isfinite(step_) ||
      step_<.02 || step_>.20 || in.get()!='\n') {throw std::runtime_error("Invalid surveyed ground header");}
    heights_.resize(static_cast<size_t>(width_)*height_);
    if (!in.read(reinterpret_cast<char *>(heights_.data()),heights_.size()*sizeof(float))) {
      throw std::runtime_error("Truncated surveyed ground heights");
    }
    interior_.resize(heights_.size());
    if (!in.read(reinterpret_cast<char *>(interior_.data()),interior_.size())) {
      throw std::runtime_error("Truncated surveyed ground boundary mask");
    }
    patches_.resize(heights_.size());
    for (int y=1;y<height_-2;++y) {
      for (int x=1;x<width_-2;++x) {
        auto & patch=patches_[y*width_+x];
        const auto full=measure(x-1,y-1,4);
        patch.flat_span=full.second;
        patch.flat_grade=full.first;
        // A wall beside a ramp can invalidate one side of the reference.
        // Every candidate still contains all four interpolation vertices
        // and a continuous third row and column on the same support face.
        for (int j=-1;j<=0;++j) for (int i=-1;i<=0;++i) {
          patch.face_grade=std::min(patch.face_grade,measure(x+i,y+j,3).first);
        }
      }
    }
  }

  template<class Points,class MapPosition,class Scan=Points>
  size_t refine(Points & points,double sensor_x,double sensor_y,double max_grade,MapPosition to_map,
    const Scan * same_stamp_scan=nullptr) const
  {
    if (!(max_grade>0)) {return 0;}
    std::vector<std::pair<int,double>> samples;
    for (const auto & p : points) {
      if (!(p.intensity>=0 && p.intensity<.035) || std::hypot(p.x-sensor_x,p.y-sensor_y)>3.) {continue;}
      const auto xyz=to_map(p);
      const auto h=height(xyz[0],xyz[1],max_grade,true);
      if (h && std::abs(xyz[2]-*h)<.45) {
        const int col=static_cast<int>((xyz[0]-x0_)/step_);
        const int row=static_cast<int>((xyz[1]-y0_)/step_);
        samples.emplace_back(row*width_+col,xyz[2]-*h);
      }
    }
    // A dense scan of a single patch must not set the height of the whole cloud.
    std::sort(samples.begin(),samples.end());
    std::vector<double> offsets;
    std::vector<std::array<double,3>> anchors;
    for (size_t i=0;i<samples.size();) {
      size_t end=i+1;
      while (end<samples.size() && samples[end].first==samples[i].first) {++end;}
      const auto & sample=samples[i+(end-i)/2];
      offsets.push_back(sample.second);
      anchors.push_back({x0_+(sample.first%width_)*step_, y0_+(sample.first/width_)*step_, sample.second});
      i=end;
    }
    if (offsets.size()<25) {return 0;}
    const auto mid=offsets.begin()+offsets.size()/2;
    std::nth_element(offsets.begin(),mid,offsets.end());
    const double bias=*mid;
    const auto inliers=std::count_if(offsets.begin(),offsets.end(),
      [bias](double v){return std::abs(v-bias)<.032;});
    if (static_cast<size_t>(inliers)*3<offsets.size()*2) {return 0;}
    // Fit a bounded attitude residual to independent flat support cells.
    // Use the median alone when the anchors occupy a line or the held-out
    // cells do not confirm a better fit.
    double mx=0,my=0,mz=0,weight=0;
    for (size_t i=0;i<anchors.size();++i) {
      const auto & a=anchors[i];
      if (i%3==0 || std::abs(a[2]-bias)>.065) {continue;}
      mx+=a[0];my+=a[1];mz+=a[2];++weight;
    }
    double gx=0,gy=0,intercept=bias;
    if (weight>=24) {
      mx/=weight;my/=weight;mz/=weight;
      double xx=0,xy=0,yy=0,xz=0,yz=0;
      for (size_t i=0;i<anchors.size();++i) {
        const auto & a=anchors[i];
        if (i%3==0 || std::abs(a[2]-bias)>.065) {continue;}
        const double x=a[0]-mx,y=a[1]-my,z=a[2]-mz;
        xx+=x*x;xy+=x*y;yy+=y*y;xz+=x*z;yz+=y*z;
      }
      const double det=xx*yy-xy*xy;
      if (det>.015*weight*(xx+yy)) {
        const double sx=(yy*xz-xy*yz)/det,sy=(xx*yz-xy*xz)/det;
        double before=0,after=0;
        for (size_t i=0;i<anchors.size();i+=3) {
          const auto & a=anchors[i];
          before+=std::min(.065,std::abs(a[2]-bias));
          after+=std::min(.065,std::abs(a[2]-mz-sx*(a[0]-mx)-sy*(a[1]-my)));
        }
        if (std::hypot(sx,sy)<.08 && after<.8*before) {gx=sx;gy=sy;intercept=mz;}
      }
    }
    const auto corrected_z=[=](const auto & xyz) {
        return xyz[2]-bias-std::clamp(intercept-bias+gx*(xyz[0]-mx)+gy*(xyz[1]-my),-.045,.045);
      };
    std::array<double,2> shift{};
    if (align_xy_) {shift=estimate_shift(points,sensor_x,sensor_y,max_grade,to_map,corrected_z);}
    size_t count=0;
    std::optional<std::vector<Observed>> observed;
    for (auto & p : points) {
      if (!(p.intensity>=.1f)) {continue;}
      // The vertical reference is observed within three metres. A small
      // attitude error can project a distant wall onto the floor outside
      // that observed neighborhood, especially in a rolling scan.
      if (std::hypot(p.x-sensor_x,p.y-sensor_y)>3.0) {continue;}
      const auto xyz=to_map(p);
      const auto h=height(xyz[0],xyz[1],max_grade,false);
      if (!h) {continue;}
      const double z=corrected_z(xyz);
      const double residual=z-*h;
      double dz=std::abs(residual);
      // Require support both at the original hit and along the full shift.
      // A point already well above the original face cannot be reclassified.
      if ((shift[0]!=0 || shift[1]!=0) && residual<=.10) {
        const auto aligned=shifted_height(xyz[0],xyz[1],*h,shift,max_grade);
        if (aligned) {dz=std::abs(z-*aligned);}
      }
      // A bracketed same-frame plane can corroborate a missed mapped slope face.
      if (same_stamp_scan && dz>.035 && residual>.035 && residual<=.106) {
        const auto g=gradient(xyz[0],xyz[1]);
        const double grade=std::hypot(g[0],g[1]);
        if (grade>=.08 && grade<max_grade) {
          if (!observed) {
            observed=observations(*same_stamp_scan,sensor_x,sensor_y,max_grade,bias,to_map);
          }
          if (const auto verified=observedResidual(*observed,xyz,g,grade,max_grade)) {dz=*verified;}
        }
      }
      if (dz<=.035) {p.intensity=static_cast<float>(dz); ++count;}
    }
    return count;
  }
private:
  struct Observed {
    double x,y,z,gx,gy;
    int cell_x,cell_y;
  };
  struct PlaneAnchor {
    double x,y,z,level;
    bool held,train;
  };
  using Plane = std::array<double,3>;  // x and y gradients, height at the query
  struct Patch {
    float flat_grade{std::numeric_limits<float>::infinity()};
    float flat_span{std::numeric_limits<float>::infinity()};
    float face_grade{std::numeric_limits<float>::infinity()};
  };
  int width_{},height_{};
  bool align_xy_{};
  double x0_{},y0_{},step_{};
  std::vector<float> heights_;
  std::vector<unsigned char> interior_;
  std::vector<Patch> patches_;
  template<class Scan,class MapPosition>
  std::vector<Observed> observations(const Scan & scan,double x,double y,double max_grade,double bias,
    MapPosition to_map) const
  {
    std::vector<Observed> samples;
    for (const auto & point : scan) {
      if (!std::isfinite(point.z) || !(std::hypot(point.x-x,point.y-y)<=3.5)) {continue;}
      const auto q=to_map(point);
      const auto h=height(q[0],q[1],max_grade,false);
      if (!h) {continue;}
      const double error=q[2]-*h-bias;
      if (!(error>=-.10 && error<=.13)) {continue;}
      const auto g=gradient(q[0],q[1]);
      if (!(std::hypot(g[0],g[1])>.06)) {continue;}
      samples.push_back({q[0],q[1],q[2],g[0],g[1],
        static_cast<int>(std::floor(q[0]/.07)),static_cast<int>(std::floor(q[1]/.07))});
    }
    std::stable_sort(samples.begin(),samples.end(),[](const auto & a,const auto & b) {
      if (a.cell_x!=b.cell_x) {return a.cell_x<b.cell_x;}
      if (a.cell_y!=b.cell_y) {return a.cell_y<b.cell_y;}
      return a.z<b.z;
    });
    samples.erase(std::unique(samples.begin(),samples.end(),[](const auto & a,const auto & b) {
      return a.cell_x==b.cell_x && a.cell_y==b.cell_y;
    }),samples.end());
    return samples;
  }

  static double quantile(const std::vector<double> & ordered,double fraction)
  {
    const double slot=(ordered.size()-1)*fraction;
    const size_t i=static_cast<size_t>(slot);
    return ordered[i]+(slot-i)*(ordered[std::min(i+1,ordered.size()-1)]-ordered[i]);
  }

  static std::optional<Plane> fitPlane(const std::vector<PlaneAnchor> & anchors)
  {
    double x=0,y=0,z=0,count=0;
    for (const auto & a : anchors) {
      if (!a.train) {continue;}
      x+=a.x; y+=a.y; z+=a.z; ++count;
    }
    if (count<6) {return std::nullopt;}
    x/=count; y/=count; z/=count;
    double xx=0,xy=0,yy=0,xz=0,yz=0;
    for (const auto & a : anchors) {
      if (!a.train) {continue;}
      const double dx=a.x-x,dy=a.y-y,dz=a.z-z;
      xx+=dx*dx; xy+=dx*dy; yy+=dy*dy; xz+=dx*dz; yz+=dy*dz;
    }
    const double det=xx*yy-xy*xy;
    if (det<=1e-10) {return std::nullopt;}
    const double gx=(yy*xz-xy*yz)/det, gy=(xx*yz-xy*xz)/det;
    return Plane{gx,gy,z-gx*x-gy*y};
  }

  static std::optional<double> observedResidual(const std::vector<Observed> & observed,
    const std::array<double,3> & point,const std::array<double,2> & g,double grade,double max_grade)
  {
    const double nx=g[0]/grade,ny=g[1]/grade;
    std::vector<PlaneAnchor> anchors;
    std::vector<double> along,crossfall,training;
    double closest=.55;
    // The samples are ordered by x cell. Inspect only cells intersecting the query's x span.
    const int first=static_cast<int>(std::floor((point[0]-.55)/.07));
    const auto begin=std::lower_bound(observed.begin(),observed.end(),first,
      [](const auto & sample,int cell){return sample.cell_x<cell;});
    for (auto it=begin;it!=observed.end();++it) {
      const auto & a=*it;
      if (a.cell_x>std::floor((point[0]+.55)/.07)) {break;}
      const double x=a.x-point[0],y=a.y-point[1],distance=std::hypot(x,y);
      if (distance<.06 || distance>.55 || std::hypot(a.gx-g[0],a.gy-g[1])>.24) {continue;}
      const bool held=(a.cell_x*7+a.cell_y*11)%3==0;
      const double level=a.z-x*g[0]-y*g[1];
      anchors.push_back({x,y,a.z,level,held,!held});
      if (!held) {training.push_back(level);}
      along.push_back(nx*x+ny*y);
      crossfall.push_back(-ny*x+nx*y);
      closest=std::min(closest,distance);
    }
    if (training.size()<6 || anchors.size()-training.size()<3 || closest>=.17) {return std::nullopt;}
    std::sort(along.begin(),along.end());
    std::sort(crossfall.begin(),crossfall.end());
    if (quantile(along,.1)>-.036 || quantile(along,.9)<.036 ||
      quantile(crossfall,.1)>-.036 || quantile(crossfall,.9)<.036) {return std::nullopt;}

    std::sort(training.begin(),training.end());
    const double level=quantile(training,.5);
    for (auto & a : anchors) {a.train=a.train && std::abs(a.level-level)<=.045;}
    auto plane=fitPlane(anchors);
    if (!plane) {return std::nullopt;}
    const auto error=[](const PlaneAnchor & a,const Plane & p) {
      return std::abs(a.z-a.x*p[0]-a.y*p[1]-p[2]);
    };
    for (auto & a : anchors) {a.train=a.train && error(a,*plane)<=.024;}
    plane=fitPlane(anchors);
    if (!plane || std::hypot((*plane)[0]-g[0],(*plane)[1]-g[1])>.17 ||
      std::hypot((*plane)[0],(*plane)[1])>max_grade) {return std::nullopt;}
    std::vector<double> holdout;
    size_t close=0;
    for (const auto & a : anchors) {
      if (!a.held) {continue;}
      holdout.push_back(error(a,*plane));
      close+=holdout.back()<=.025;
    }
    if (4*close<3*holdout.size()) {return std::nullopt;}
    std::sort(holdout.begin(),holdout.end());
    const double dz=std::abs(point[2]-(*plane)[2]);
    if (quantile(holdout,.5)>.019 || dz>.019) {return std::nullopt;}
    return dz;
  }

  // Call only after height() accepted the cell and its four finite vertices.
  std::array<double,2> gradient(double x,double y) const
  {
    const double u=(x-x0_)/step_,v=(y-y0_)/step_;
    const int i=static_cast<int>(u),j=static_cast<int>(v);
    const double a=u-i,b=v-j;
    const double z00=at(i,j),z10=at(i+1,j),z01=at(i,j+1),z11=at(i+1,j+1);
    return {((1-b)*(z10-z00)+b*(z11-z01))/step_,
      ((1-a)*(z01-z00)+a*(z11-z10))/step_};
  }
  float at(int x,int y) const {return heights_[y*width_+x];}
  std::optional<double> shifted_height(double x,double y,double h,
    const std::array<double,2> & shift,double max_grade) const
  {
    const int steps=static_cast<int>(std::ceil(2*std::hypot(shift[0],shift[1])/step_));
    double estimate=h;
    for (int i=1;i<=steps;++i) {
      const double f=static_cast<double>(i)/steps;
      const auto next=height(x+f*shift[0],y+f*shift[1],max_grade,false);
      if (!next || std::abs(*next-h)>.075) {return std::nullopt;}
      estimate=*next;
    }
    return estimate;
  }

  template<class Points,class MapPosition,class VerticalRegistration>
  std::array<double,2> estimate_shift(const Points & points,double sensor_x,double sensor_y,
    double max_grade,MapPosition to_map,VerticalRegistration corrected_z) const
  {
    struct Sample {
      int cell;
      double x,y,h,residual,gx,gy;
    };
    std::vector<Sample> samples;
    const double d=step_*.25;
    for (const auto & p : points) {
      if (!(p.intensity>=0 && p.intensity<.035) || std::hypot(p.x-sensor_x,p.y-sensor_y)>3.) {continue;}
      const auto xyz=to_map(p);
      const double x=xyz[0],y=xyz[1];
      const auto h=height(x,y,max_grade,false);
      if (!h) {continue;}
      const double residual=corrected_z(xyz)-*h;
      if (!(std::abs(residual)<.15)) {continue;}
      const auto east=height(x+d,y,max_grade,false),west=height(x-d,y,max_grade,false);
      const auto north=height(x,y+d,max_grade,false),south=height(x,y-d,max_grade,false);
      if (!east || !west || !north || !south) {continue;}
      const double gx=(*east-*west)/(2*d),gy=(*north-*south)/(2*d);
      if (std::hypot(gx,gy)<.08) {continue;}
      const int col=static_cast<int>((x-x0_)/step_),row=static_cast<int>((y-y0_)/step_);
      samples.push_back({row*width_+col,x,y,*h,residual,gx,gy});
    }
    std::sort(samples.begin(),samples.end(),[](const auto & a,const auto & b) {
        return std::pair{a.cell,a.residual}<std::pair{b.cell,b.residual};
      });
    size_t cells=0;
    for (size_t start=0;start<samples.size();) {
      size_t end=start+1;
      while (end<samples.size() && samples[end].cell==samples[start].cell) {++end;}
      samples[cells++]=samples[start+(end-start)/2];
      start=end;
    }
    if (cells<18) {return {};}
    // Ridge regularization suppresses the unobserved component on single ramps.
    double xx=0,xy=0,yy=0,xz=0,yz=0;
    for (size_t i=0;i<cells;++i) {
      if (i%3==0) {continue;}
      const auto & s=samples[i];
      xx+=s.gx*s.gx;xy+=s.gx*s.gy;yy+=s.gy*s.gy;
      xz+=s.gx*s.residual;yz+=s.gy*s.residual;
    }
    const double ridge=.08*(xx+yy);
    xx+=ridge;yy+=ridge;
    const double det=xx*yy-xy*xy;
    std::array<double,2> shift{(yy*xz-xy*yz)/det,(xx*yz-xy*xz)/det};
    const double norm=std::hypot(shift[0],shift[1]);
    if (!(norm>.01)) {return {};}
    if (norm>.25) {shift[0]*=.25/norm;shift[1]*=.25/norm;}
    double before=0,after=0;
    size_t valid=0,held_out=0;
    for (size_t i=0;i<cells;i+=3) {
      const auto & s=samples[i];
      const auto aligned=shifted_height(s.x,s.y,s.h,shift,max_grade);
      const double base=std::abs(s.residual);
      const double next=aligned ? std::abs(s.residual+s.h-*aligned) : base;
      before+=std::min(base,.10);
      after+=std::min(next,.10);
      valid+=aligned.has_value();
      ++held_out;
    }
    if (valid*3<held_out*2 || after>=.85*before || before-after<.002*held_out) {return {};}
    return shift;
  }

  std::pair<float,float> measure(int x,int y,int n) const
  {
    const float inf=std::numeric_limits<float>::infinity();
    float lo=inf,hi=-inf,dx=0,dy=0;
    for (int j=0;j<n;++j) for (int i=0;i<n;++i) {
      const float sample=at(x+i,y+j);
      if (!std::isfinite(sample)) {return {inf,inf};}
      lo=std::min(lo,sample); hi=std::max(hi,sample);
      if (i+1<n) {dx=std::max(dx,std::abs(sample-at(x+i+1,y+j)));}
      if (j+1<n) {dy=std::max(dy,std::abs(sample-at(x+i,y+j+1)));}
    }
    return {static_cast<float>(std::hypot(dx,dy)/step_),hi-lo};
  }
  std::optional<double> height(double x,double y,double grade,bool flat) const
  {
    const double u=(x-x0_)/step_,v=(y-y0_)/step_;
    if (!(u>=1 && v>=1 && u<width_-2 && v<height_-2)) {return std::nullopt;}
    const int i=static_cast<int>(u),j=static_cast<int>(v);
    // The wall margin applies at the hit itself. Neighboring samples define
    // the shape of the surface and do not add another margin around the hit.
    if (!interior_[static_cast<int>(v+.5)*width_+static_cast<int>(u+.5)]) {return std::nullopt;}
    const auto & patch=patches_[j*width_+i];
    if (flat ? (patch.flat_grade>grade || patch.flat_span>.045) : patch.face_grade>grade) {return std::nullopt;}
    const double fx=u-i,fy=v-j;
    return (1-fy)*((1-fx)*at(i,j)+fx*at(i+1,j))+fy*((1-fx)*at(i,j+1)+fx*at(i+1,j+1));
  }
};
}  // namespace terrain_analysis
