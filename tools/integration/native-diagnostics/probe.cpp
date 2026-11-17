#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <dbghelp.h>
#include <cstdio>
#include <rclcpp/rclcpp.hpp>
#include <diagnostic_updater/diagnostic_updater.hpp>

LONG WINAPI trace_exception(EXCEPTION_POINTERS* error){
  std::fprintf(stderr,"exception=%08lx address=%p\n",error->ExceptionRecord->ExceptionCode,error->ExceptionRecord->ExceptionAddress);
  HANDLE process=GetCurrentProcess();SymInitialize(process,nullptr,TRUE);
  CONTEXT context=*error->ContextRecord;STACKFRAME64 frame{};
  frame.AddrPC.Offset=context.Rip;frame.AddrFrame.Offset=context.Rbp;frame.AddrStack.Offset=context.Rsp;
  frame.AddrPC.Mode=frame.AddrFrame.Mode=frame.AddrStack.Mode=AddrModeFlat;
  for(int i=0;i<32&&frame.AddrPC.Offset;++i){
    char storage[sizeof(SYMBOL_INFO)+256]{};auto* symbol=reinterpret_cast<SYMBOL_INFO*>(storage);symbol->SizeOfStruct=sizeof(SYMBOL_INFO);symbol->MaxNameLen=255;DWORD64 displacement=0;
    IMAGEHLP_MODULE64 module{};module.SizeOfStruct=sizeof(module);SymGetModuleInfo64(process,frame.AddrPC.Offset,&module);
    if(SymFromAddr(process,frame.AddrPC.Offset,&displacement,symbol))std::fprintf(stderr,"%s!%s+%llu\n",module.ModuleName,symbol->Name,displacement);
    else std::fprintf(stderr,"%s!%llx\n",module.ModuleName,frame.AddrPC.Offset);
    if(!StackWalk64(IMAGE_FILE_MACHINE_AMD64,process,GetCurrentThread(),&frame,&context,nullptr,SymFunctionTableAccess64,SymGetModuleBase64,nullptr))break;
  }
  std::fflush(stderr);return EXCEPTION_EXECUTE_HANDLER;
}
struct Probe : diagnostic_updater::Updater {
  using Updater::Updater;
  void check_lock(){std::puts("before_updater_mutex");std::fflush(stdout);lock_.lock();lock_.unlock();std::puts("after_updater_mutex");std::fflush(stdout);}
};
int main(int argc,char** argv){
  SetEnvironmentVariableA("ROS_LOCALHOST_ONLY","1");SetEnvironmentVariableA("ROS_DOMAIN_ID","176");
  SetErrorMode(SEM_NOGPFAULTERRORBOX);SetUnhandledExceptionFilter(trace_exception);
  rclcpp::init(argc,argv);auto node=std::make_shared<rclcpp::Node>("diagnostics_probe");
  if(argc>1&&std::string(argv[1])=="plain"){
    diagnostic_updater::Updater updater(node.get());std::puts("plain_updater_constructed");std::fflush(stdout);
    updater.setHardwareID("probe");std::puts("before_plain_add");std::fflush(stdout);
    updater.add("probe",[](diagnostic_updater::DiagnosticStatusWrapper& status){status.summary(0,"ready");});
    std::puts("after_plain_add");std::fflush(stdout);updater.force_update();rclcpp::shutdown();return 0;
  }
  Probe updater(node.get());std::puts("updater_constructed");std::fflush(stdout);
  updater.setHardwareID("probe");updater.check_lock();
  std::puts("before_add");std::fflush(stdout);
  updater.add("probe",[](diagnostic_updater::DiagnosticStatusWrapper& status){status.summary(0,"ready");});
  std::puts("after_add");std::fflush(stdout);updater.force_update();rclcpp::shutdown();return 0;
}
