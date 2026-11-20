#include <mujoco/mujoco.h>
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#else
#include <dlfcn.h>
#endif
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef RMSIM_EXPECTED_RUNTIME
#define RMSIM_EXPECTED_RUNTIME "4.0.1+rmsim.static-world-flex.3"
#endif

#define API_FUNCTIONS(X) \
  X(mj_loadXML) X(mj_makeData) X(mj_deleteData) X(mj_deleteModel) \
  X(mj_step) X(mj_forward) X(mj_flex) X(mj_collision) X(mj_setConst) \
  X(mj_resetData) X(mj_resetDataDebug) X(mj_copyData) X(mj_copyModel) \
  X(mj_stateSize) X(mj_getState) X(mj_setState) X(mj_saveModel) X(mj_loadModel) \
  X(mj_version) X(mj_versionString) X(mj_parseXML) X(mj_compile) X(mj_recompile) X(mj_deleteSpec) X(mju_malloc)

struct API {
  void* handle;
  std::string loaded_path;
#define DECLARE(name) decltype(&::name) name;
  API_FUNCTIONS(DECLARE)
#undef DECLARE
  using InvalidateFn=void (*)(mjModel*);
  InvalidateFn invalidate;
  explicit API(const char* path) {
#ifdef _WIN32
    handle=reinterpret_cast<void*>(LoadLibraryA(path));
    if(!handle)throw std::runtime_error("LoadLibrary failed: "+std::to_string(GetLastError()));
    char module_path[32768]={0};
    if(!GetModuleFileNameA(static_cast<HMODULE>(handle),module_path,sizeof(module_path)))
      throw std::runtime_error("GetModuleFileName failed");
    loaded_path=module_path;
    auto symbol=[&](const char* name){return reinterpret_cast<void*>(GetProcAddress(static_cast<HMODULE>(handle),name));};
#else
    handle=dlopen(path,RTLD_NOW|RTLD_LOCAL);
    if(!handle)throw std::runtime_error(dlerror());
    loaded_path=path;
    auto symbol=[&](const char* name){return dlsym(handle,name);};
#endif
#define LOAD(name) name=reinterpret_cast<decltype(name)>(symbol(#name)); if(!name)throw std::runtime_error(#name);
    API_FUNCTIONS(LOAD)
#undef LOAD
    invalidate=reinterpret_cast<decltype(invalidate)>(symbol("mjrmsim_invalidateStaticFlex"));
  }
};

std::string escaped(const std::string& value) {
  std::string out;
  for(unsigned char c:value) {
    if(c=='"'||c=='\\') {out+='\\';out+=c;}
    else if(c=='\n')out+="\\n";
    else if(c=='\r')out+="\\r";
    else if(c=='\t')out+="\\t";
    else out+=c;
  }
  return out;
}

struct Sim {
  API& a;
  mjModel* m=nullptr;
  mjData* d=nullptr;
  explicit Sim(API& api,const char* file):a(api) {
    char err[2048]={0};m=a.mj_loadXML(file,nullptr,err,sizeof(err));
    if(!m)throw std::runtime_error(err);
    d=a.mj_makeData(m);
    // Compile with a BVH, then disable midphase before the very first forward.
    // Upstream cannot enable a BVH that was omitted by compilation entirely.
    if(std::string(file).find("initial_midphase_disabled")!=std::string::npos)
      m->opt.disableflags|=mjDSBL_MIDPHASE;
    a.mj_forward(m,d);
  }
  ~Sim(){if(d)a.mj_deleteData(d);if(m)a.mj_deleteModel(m);}
};

static long long values_compared=0;
static long long bitwise_differences=0;
static double max_difference=0;

void compare(const mjtNum* x,const mjtNum* y,size_t n,const char* field,int step) {
  values_compared+=n;
  if(!std::memcmp(x,y,n*sizeof(mjtNum)))return;
  for(size_t i=0;i<n;++i) {
    if(std::memcmp(x+i,y+i,sizeof(mjtNum)))++bitwise_differences;
    double diff=std::abs(x[i]-y[i]);max_difference=std::max(max_difference,diff);
    if(!std::isfinite(x[i])||!std::isfinite(y[i])||diff>1e-12) {
      std::fprintf(stderr,"DIFF step=%d field=%s index=%zu raw=%.17g patched=%.17g abs=%.17g\n",step,field,i,x[i],y[i],diff);
      throw std::runtime_error("numerical mismatch");
    }
  }
}

void compare_state(const Sim& x,const Sim& y,int step,bool full_geometry=true) {
  const mjModel* m=x.m;const mjData* a=x.d;const mjData* b=y.d;
  if(a->ncon!=b->ncon||a->nefc!=b->nefc)throw std::runtime_error("contact/constraint count mismatch");
#define CMP(field,n) compare(a->field,b->field,(n),#field,step)
  compare(&a->time,&b->time,1,"time",step);
  CMP(qpos,m->nq);CMP(qvel,m->nv);CMP(qacc,m->nv);CMP(qacc_warmstart,m->nv);
  CMP(qfrc_constraint,m->nv);CMP(qfrc_bias,m->nv);CMP(xpos,3*m->nbody);
  CMP(xquat,4*m->nbody);CMP(geom_xpos,3*m->ngeom);CMP(geom_xmat,9*m->ngeom);
  CMP(sensordata,m->nsensordata);
  if(full_geometry) {
    CMP(flexvert_xpos,3*m->nflexvert);CMP(flexelem_aabb,6*m->nflexelem);
    CMP(flexedge_velocity,m->nflexedge);
    CMP(bvh_aabb_dyn,6*m->nbvhdynamic);
  }
  for(int i=0;i<a->ncon;++i) {
    const mjContact& ac=a->contact[i];const mjContact& bc=b->contact[i];
    if(ac.dim!=bc.dim||ac.efc_address!=bc.efc_address||
       std::memcmp(ac.geom,bc.geom,sizeof(ac.geom))||
       std::memcmp(ac.flex,bc.flex,sizeof(ac.flex))||
       std::memcmp(ac.elem,bc.elem,sizeof(ac.elem)))throw std::runtime_error("contact identity mismatch");
    compare(&ac.dist,&bc.dist,1,"contact.dist",step);
    compare(ac.pos,bc.pos,3,"contact.pos",step);
    compare(ac.frame,bc.frame,9,"contact.frame",step);
    compare(ac.friction,bc.friction,5,"contact.friction",step);
    compare(ac.solref,bc.solref,mjNREF,"contact.solref",step);
    compare(ac.solimp,bc.solimp,mjNIMP,"contact.solimp",step);
  }
  for(int i=0;i<mjNWARNING;++i)if(a->warning[i].number||b->warning[i].number)
    throw std::runtime_error("MuJoCo warning");
#undef CMP
}

void lifecycle(Sim& s,int step,const std::string& temp_prefix) {
  if(step==0 && temp_prefix.find("initial_midphase_disabled")!=std::string::npos)
    s.m->opt.disableflags&=~mjDSBL_MIDPHASE;
  const int sig=mjSTATE_INTEGRATION;
  auto saved=[&](){std::vector<mjtNum> state(s.a.mj_stateSize(s.m,sig));s.a.mj_getState(s.m,s.d,state.data(),sig);return state;};
  if(step==250) {
    auto state=saved();s.a.mj_resetDataDebug(s.m,s.d,0xAB);s.a.mj_setState(s.m,s.d,state.data(),sig);
  }
  if(step==500) {
    mjData* replacement=s.a.mj_copyData(nullptr,s.m,s.d);s.a.mj_deleteData(s.d);s.d=replacement;
  }
  if(step==750) {
    // mj_setConst runs on a different mjData. All other live data must invalidate.
    for(int f=0;f<s.m->nflex;++f)for(int i=s.m->flex_vertadr[f];i<s.m->flex_vertadr[f]+s.m->flex_vertnum[f];++i)
      if(s.m->flex_vertbodyid[i]==0)s.m->flex_vert[3*i+2]+=.003;
    mjData* scratch=s.a.mj_makeData(s.m);s.a.mj_setConst(s.m,scratch);s.a.mj_deleteData(scratch);
  }
  if(step==1000) {
    // Copy into an existing model object: old data still points to the same address.
    mjModel* source=s.a.mj_copyModel(nullptr,s.m);
    s.a.mj_copyModel(s.m,source);s.a.mj_deleteModel(source);
  }
  if(step==1250) {
    auto state=saved();std::string path=temp_prefix+".mjb";
    s.a.mj_saveModel(s.m,path.c_str(),nullptr,0);
    mjModel* replacement=s.a.mj_loadModel(path.c_str(),nullptr);
    if(!replacement)throw std::runtime_error("MJB reload failed");
    s.a.mj_deleteData(s.d);s.a.mj_deleteModel(s.m);s.m=replacement;
    s.d=s.a.mj_makeData(s.m);s.a.mj_setState(s.m,s.d,state.data(),sig);
  }
  if(step==1500)s.m->opt.disableflags|=mjDSBL_MIDPHASE;
  if(step==1503)s.m->opt.disableflags&=~mjDSBL_MIDPHASE;
  if(step==1750) {
    for(int f=0;f<s.m->nflex;++f)s.m->flex_radius[f]*=1.1;
    if(s.a.invalidate)s.a.invalidate(s.m);
    else {mjData* scratch=s.a.mj_makeData(s.m);s.a.mj_setConst(s.m,scratch);s.a.mj_deleteData(scratch);}
  }
  if(step==1900) {
    // Match the official Python RecompileSpec allocation, which supplies only
    // sizeof(mjModel). Private metadata must live in its engine-owned buffer.
    auto marker=temp_prefix.rfind('.');std::string xml=temp_prefix.substr(0,marker);
    char error[2048]={0};mjSpec* spec=s.a.mj_parseXML(xml.c_str(),nullptr,error,sizeof(error));
    if(!spec)throw std::runtime_error(error);
    // Recompile requires an already-compiled spec, so its element/state mapping
    // exists. This is also the state of a Python MjSpec before .recompile().
    mjModel* spec_model=s.a.mj_compile(spec,nullptr);
    if(!spec_model)throw std::runtime_error("initial spec compilation failed");
    mjModel* fresh=static_cast<mjModel*>(s.a.mju_malloc(sizeof(mjModel)));
    std::memset(fresh,0,sizeof(mjModel));
    mjData* fresh_data=s.a.mj_copyData(nullptr,s.m,s.d);
    if(s.a.mj_recompile(spec,nullptr,fresh,fresh_data))throw std::runtime_error("external-struct recompile failed");
    s.a.mj_deleteModel(spec_model);
    s.a.mj_deleteSpec(spec);s.a.mj_deleteData(s.d);s.a.mj_deleteModel(s.m);
    s.m=fresh;s.d=fresh_data;
  }
}

int main(int argc,char** argv) {
  try {
    if(argc<5)throw std::runtime_error("usage: native_compare compare|compare_model|bench raw_library patched_library model.xml [steps] [warmup]");
    std::string mode=argv[1];int steps=argc>5?std::atoi(argv[5]):2000;
    if(mode=="compare"||mode=="compare_model") {
      API raw(argv[2]),patched(argv[3]);Sim x(raw,argv[4]),y(patched,argv[4]);
      if(raw.mj_version()!=4000001||patched.mj_version()!=4000001)
        throw std::runtime_error("unexpected C API version");
      if(std::string(raw.mj_versionString())!="4.0.1"||
         std::string(patched.mj_versionString())!=RMSIM_EXPECTED_RUNTIME||
         !patched.invalidate)throw std::runtime_error("runtime identity mismatch");
      compare_state(x,y,-1);long long contacts=0;
      for(int i=0;i<steps;++i) {
        if(mode=="compare") {
          lifecycle(x,i,std::string(argv[4])+".raw");lifecycle(y,i,std::string(argv[4])+".patched");
        }
        for(Sim* s:{&x,&y}) {
          if(mode=="compare") {
            const double t=i*.001;
            s->d->xfrc_applied[6+0]=1.2*std::sin(2.1*t);
            s->d->xfrc_applied[6+1]=.9*std::cos(1.3*t);
            s->d->xfrc_applied[6+5]=.015*std::sin(3.7*t);
          }
          s->a.mj_step(s->m,s->d);
        }
        compare_state(x,y,i,mode=="compare"||i+1==steps);contacts+=x.d->ncon;
      }
      std::printf("{\"mode\":\"%s\",\"model\":\"%s\",\"steps\":%d,\"values_compared\":%lld,\"bitwise_differences\":%lld,\"max_abs_difference\":%.17g,\"accumulated_contacts\":%lld,\"raw_version\":%d,\"patched_version\":%d,\"raw_runtime\":\"%s\",\"patched_runtime\":\"%s\",\"raw_library\":\"%s\",\"patched_library\":\"%s\",\"sizeof_model\":%zu,\"sizeof_data\":%zu,\"passed\":true}\n",mode.c_str(),escaped(argv[4]).c_str(),steps,values_compared,bitwise_differences,max_difference,contacts,raw.mj_version(),patched.mj_version(),escaped(raw.mj_versionString()).c_str(),escaped(patched.mj_versionString()).c_str(),escaped(raw.loaded_path).c_str(),escaped(patched.loaded_path).c_str(),sizeof(mjModel),sizeof(mjData));
    } else if(mode=="bench") {
      API api(argv[2]);Sim s(api,argv[4]);
      int warmup=argc>6?std::atoi(argv[6]):5;
      for(int i=0;i<warmup;++i)api.mj_step(s.m,s.d);
      auto timed=[&](auto fn){auto t=std::chrono::steady_clock::now();for(int i=0;i<steps;++i)fn();return std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-t).count()/steps;};
      double flex=timed([&]{api.mj_flex(s.m,s.d);});
      double collision=timed([&]{api.mj_collision(s.m,s.d);});
      double step=timed([&]{api.mj_step(s.m,s.d);});
      double forward=timed([&]{api.mj_step(s.m,s.d);api.mj_forward(s.m,s.d);});
      std::printf("{\"mode\":\"bench\",\"library\":\"%s\",\"runtime\":\"%s\",\"triangles\":%lld,\"vertices\":%lld,\"steps\":%d,\"warmup_steps\":%d,\"flex_ms\":%.9g,\"collision_ms\":%.9g,\"step_ms\":%.9g,\"step_forward_ms\":%.9g,\"z\":%.17g,\"contacts\":%d}\n",escaped(api.loaded_path).c_str(),escaped(api.mj_versionString()).c_str(),(long long)s.m->nflexelem,(long long)s.m->nflexvert,steps,warmup,flex,collision,step,forward,s.d->qpos[2],s.d->ncon);
    } else throw std::runtime_error("unknown mode");
  } catch(const std::exception& e) {std::fprintf(stderr,"ERROR: %s\n",e.what());return 1;}
  return 0;
}
