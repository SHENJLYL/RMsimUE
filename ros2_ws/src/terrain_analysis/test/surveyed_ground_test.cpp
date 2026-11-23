#include "terrain_analysis/surveyed_ground.hpp"
#include <filesystem>
#include <iostream>
#include <stdexcept>

struct Hit {double x,y,z; float intensity;};

int main()
{
  constexpr double step=.05;
  const auto level=[step](int x,int y) {
    const double u=x*step;
    const double slope=.28*std::clamp(u-.65,0.,.45);
    const double wave=u>1.25 ? .035*std::sin(8*(u-1.25)) : 0.;
    return float(slope+wave+(y>=32?.21:0.));
  };
  const auto file=std::filesystem::temp_directory_path()/"rmsim-surveyed-ground-test.bin";
  {
    std::ofstream out(file,std::ios::binary);
    out<<"RMSIM_SURVEY_V2 48 48 0 0 0.05\n";
    for(int y=0;y<48;++y) for(int x=0;x<48;++x) {
      const float h=x>=42?std::numeric_limits<float>::quiet_NaN():level(x,y);
      out.write(reinterpret_cast<const char *>(&h),sizeof(h));
    }
    for(int y=0;y<48;++y) for(int x=0;x<48;++x) {out.put(x!=38);}
  }
  terrain_analysis::SurveyedGround model(file.string());
  std::filesystem::remove(file);
  const auto transform=[](Hit p){return std::array<double,3>{p.x,p.y,p.z};};
  std::vector<Hit> cloud;
  constexpr double bias=.08;
  for(int y=4;y<13;++y) for(int x=3;x<12;++x) {
    cloud.push_back({x*step,y*step,level(x,y)+bias,.01f});
  }
  const size_t first=cloud.size();
  const auto add=[&](int x,int y,double above) {
    cloud.push_back({x*step,y*step,level(x,y)+bias+above,.17f});
  };
  add(17,12,0); add(32,12,0);  // Ramp and undulation.
  add(17,12,.12); add(32,12,.20); add(24,31,0); add(43,12,0); add(38,12,0);
  const auto original=cloud;
  const double grade=std::tan(35*std::acos(-1.)/180.);
  if(model.refine(cloud,0,0,grade,transform)!=2) {throw std::runtime_error("wrong correction count");}
  for(size_t i=0;i<cloud.size();++i) {
    const auto a=original[i], b=cloud[i];
    if(a.x!=b.x || a.y!=b.y || a.z!=b.z) {throw std::runtime_error("point moved");}
    const bool expected=i==first || i==first+1;
    if((a.intensity!=b.intensity)!=expected) {throw std::runtime_error("wrong ground or obstacle classification");}
  }
  std::vector<Hit> repeated(80,original[0]);
  repeated.push_back(original[first]);
  if(model.refine(repeated,0,0,grade,transform)!=0) {throw std::runtime_error("one area set the frame offset");}
  std::cout<<"Ramp, wave, raised obstacles, riser, masked region, unchanged geometry and sparse registration passed\n";
}
