#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <dbghelp.h>
#include <cstdio>
inline LONG WINAPI rmsim_trace_exception(EXCEPTION_POINTERS* error){
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
inline void rmsim_install_trace(){SetErrorMode(SEM_NOGPFAULTERRORBOX);SetUnhandledExceptionFilter(rmsim_trace_exception);}
