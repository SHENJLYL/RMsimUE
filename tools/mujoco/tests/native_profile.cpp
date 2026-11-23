// Reuse the native regression harness's strict state/contact comparator.
#define main rmsim_reference_harness_main
#ifndef RMSIM_REFERENCE_HARNESS
#define RMSIM_REFERENCE_HARNESS "native_compare.cpp"
#endif
#include RMSIM_REFERENCE_HARNESS
#undef main

static mjtNum profile_clock_ms() {
  static const auto origin=std::chrono::steady_clock::now();
  return std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-origin).count();
}

struct ProfileAPI : API {
  decltype(&::mju_threadpool) pool;
  mjfTime* timer;
  explicit ProfileAPI(const char* path) : API(path) {
#ifdef _WIN32
    pool=reinterpret_cast<decltype(pool)>(GetProcAddress(static_cast<HMODULE>(handle),"mju_threadpool"));
    timer=reinterpret_cast<mjfTime*>(GetProcAddress(static_cast<HMODULE>(handle),"mjcb_time"));
#else
    pool=reinterpret_cast<decltype(pool)>(dlsym(handle,"mju_threadpool"));
    timer=reinterpret_cast<mjfTime*>(dlsym(handle,"mjcb_time"));
#endif
    if(!pool||!timer)throw std::runtime_error("missing upstream threadpool/timer API");
  }
};

static void print_timers(const mjData* d) {
  static const char* names[mjNTIMER]={"step","forward","inverse","position","velocity","actuation","constraint","advance","kinematics","inertia","collision","make_constraints","project_constraints","broadphase","midphase_plus_narrowphase"};
  std::printf("{\"timers\":{");
  for(int i=0;i<mjNTIMER;i++) {
    std::printf("%s\"%s\":{\"calls\":%d,\"total_ms\":%.9g,\"mean_ms\":%.9g}",i?",":"",names[i],d->timer[i].number,d->timer[i].duration,d->timer[i].number?d->timer[i].duration/d->timer[i].number:0.0);
  }
  std::printf("},\"contacts\":%d,\"max_contacts\":%d,\"z\":%.17g}",d->ncon,d->maxuse_con,d->qpos[2]);
}

int main(int argc,char** argv) {
  try {
    if(argc<4)throw std::runtime_error("usage: native_profile profile|compare_threads library model.xml [steps] [warmup] [workers]");
    std::string mode=argv[1];
    const int steps=argc>4?std::atoi(argv[4]):500;
    const int warmup=argc>5?std::atoi(argv[5]):100;
    const int selected_workers=argc>6?std::atoi(argv[6]):-1;
    if(steps<1||warmup<0||selected_workers>32)throw std::runtime_error("invalid iteration/thread count");
    ProfileAPI api(argv[2]);
    std::printf("{\"mode\":\"%s\",\"library\":\"%s\",\"runtime\":\"%s\",\"steps\":%d,\"warmup_steps\":%d,\"runs\":[",mode.c_str(),escaped(api.loaded_path).c_str(),escaped(api.mj_versionString()).c_str(),steps,warmup);
    std::vector<int> workers=selected_workers>=0?std::vector<int>{selected_workers}:std::vector<int>{0,1,3,7};
    if(mode=="profile") {
      Sim s(api,argv[3]);
      *api.timer=profile_clock_ms;
      bool first=true;
      for(int n:workers) {
        api.pool(s.d,n);
        api.mj_resetData(s.m,s.d);
        api.mj_forward(s.m,s.d);
        for(int i=0;i<warmup;i++)api.mj_step(s.m,s.d);
        std::memset(s.d->timer,0,sizeof(s.d->timer));
        auto begin=std::chrono::steady_clock::now();
        for(int i=0;i<steps;i++)api.mj_collision(s.m,s.d);
        double fixed_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count()/steps;
        std::printf("%s{\"workers\":%d,\"fixed_collision_wall_ms\":%.9g,\"fixed_collision\":",first?"":",",n,fixed_ms);first=false;
        print_timers(s.d);
        std::memset(s.d->timer,0,sizeof(s.d->timer));
        begin=std::chrono::steady_clock::now();
        long long contacts=0;
        std::vector<double> step_times;step_times.reserve(steps);
        for(int i=0;i<steps;i++) {
          const auto start=std::chrono::steady_clock::now();
          api.mj_step(s.m,s.d);contacts+=s.d->ncon;
          step_times.push_back(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count());
        }
        double step_ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-begin).count()/steps;
        std::sort(step_times.begin(),step_times.end());
        auto percentile=[&](double p){return step_times[static_cast<size_t>(std::ceil(p*(steps-1)))];};
        const auto over_budget=std::count_if(step_times.begin(),step_times.end(),[&](double ms){return ms>1000*s.m->opt.timestep;});
        std::printf(",\"step_wall_ms\":%.9g,\"step_p50_ms\":%.9g,\"step_p95_ms\":%.9g,\"step_p99_ms\":%.9g,\"step_max_ms\":%.9g,\"steps_over_timestep\":%lld,\"timestep_seconds\":%.9g,\"accumulated_contacts\":%lld,\"dynamic\":",step_ms,percentile(.5),percentile(.95),percentile(.99),step_times.back(),static_cast<long long>(over_budget),s.m->opt.timestep,contacts);
        print_timers(s.d);
        std::printf("}");std::fflush(stdout);
        for(int i=0;i<mjNWARNING;i++)if(s.d->warning[i].number)throw std::runtime_error("MuJoCo warning");
      }
      *api.timer=nullptr;
      api.pool(s.d,0);
    } else if(mode=="compare_threads") {
      Sim x(api,argv[3]),y(api,argv[3]);
      bool first=true;
      for(int n:workers) {
        if(n==0)continue;
        api.pool(y.d,n);
        api.mj_resetData(x.m,x.d);api.mj_resetData(y.m,y.d);
        api.mj_forward(x.m,x.d);api.mj_forward(y.m,y.d);
        values_compared=0;bitwise_differences=0;max_difference=0;
        compare_state(x,y,-1);
        long long contacts=0;
        for(int i=0;i<steps;i++) {
          api.mj_step(x.m,x.d);api.mj_step(y.m,y.d);
          compare_state(x,y,i,i+1==steps);contacts+=x.d->ncon;
        }
        std::printf("%s{\"workers\":%d,\"values_compared\":%lld,\"bitwise_differences\":%lld,\"max_abs_difference\":%.17g,\"accumulated_contacts\":%lld}",first?"":",",n,values_compared,bitwise_differences,max_difference,contacts);first=false;
        std::fflush(stdout);
      }
      api.pool(y.d,0);
    } else throw std::runtime_error("unknown mode");
    std::printf("],\"passed\":true}\n");
  } catch(const std::exception& e) {std::fprintf(stderr,"ERROR: %s\n",e.what());return 1;}
}
