#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <mutex>
int main(){
    char module[MAX_PATH]={0};GetModuleFileNameA(GetModuleHandleA("msvcp140.dll"),module,MAX_PATH);
    std::printf("loaded_msvcp140=%s\n",module);std::fflush(stdout);
    std::mutex mutex;std::puts("before_lock");std::fflush(stdout);
    mutex.lock();std::puts("after_lock");mutex.unlock();return 0;
}
