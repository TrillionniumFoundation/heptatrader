// Tiny DecisionCell prediction head: real HIP forward, backprop and SGD.
// Architecture: 32 causal/native-Neuron inputs -> 16 tanh -> 2 returns.
// No trading/model API, no CPU training fallback, bounded allocation/iterations.
#include <hip/hip_runtime.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <thread>
#include <chrono>
constexpr int F=32,H=16,O=2,P=F*H+H+H*O+O;
static void check(hipError_t e){if(e!=hipSuccess)throw std::runtime_error(hipGetErrorString(e));}
struct Buffer {float* p=nullptr; explicit Buffer(size_t n){check(hipMalloc(&p,n*sizeof(float)));}
 ~Buffer(){if(p){auto ignored=hipFree(p);(void)ignored;}}};
__device__ float huber(float d){float a=fabsf(d);return a<=1?0.5f*d*d:a-0.5f;}
__device__ float dhuber(float d){return fminf(1,fmaxf(-1,d));}
__global__ void forward_backward(const float* x,const float* y,const float* m,
 const float* teacher,const float* tm,const float* w,float* predictions,float* losses,float* sample_g,int n){
 int s=blockIdx.x*blockDim.x+threadIdx.x;if(s>=n)return;
 float z[H],dp[O],back[H];
 for(int j=0;j<H;j++){float a=w[F*H+j];for(int i=0;i<F;i++)a+=x[s*F+i]*w[i*H+j];z[j]=tanhf(a);}
 for(int k=0;k<O;k++){
  float p=w[F*H+H+H*O+k];for(int j=0;j<H;j++)p+=z[j]*w[F*H+H+j*O+k];
  predictions[s*O+k]=p;float d=p-y[s*O+k],td=p-teacher[s*O+k];
  losses[s*O+k]=m[s*O+k]*(huber(d)+0.1f*tm[s*O+k]*huber(td));
  dp[k]=m[s*O+k]*(dhuber(d)+0.1f*tm[s*O+k]*dhuber(td));
 }
 for(int j=0;j<H;j++){float v=0;for(int k=0;k<O;k++)v+=dp[k]*w[F*H+H+j*O+k];back[j]=v*(1-z[j]*z[j]);}
 float* g=sample_g+s*P;
 for(int i=0;i<F;i++)for(int j=0;j<H;j++)g[i*H+j]=x[s*F+i]*back[j];
 for(int j=0;j<H;j++)g[F*H+j]=back[j];
 for(int j=0;j<H;j++)for(int k=0;k<O;k++)g[F*H+H+j*O+k]=z[j]*dp[k];
 for(int k=0;k<O;k++)g[F*H+H+H*O+k]=dp[k];
}
__global__ void reduce_gradient(const float* sg,float* g,int n,float denom){
 int p=blockIdx.x*blockDim.x+threadIdx.x;if(p>=P)return;
 float v=0;for(int s=0;s<n;s++)v+=sg[s*P+p];g[p]=v/denom;
}
__global__ void update(float* w,const float* g,float lr){
 int p=blockIdx.x*blockDim.x+threadIdx.x;if(p>=P)return;
 w[p]-=lr*fminf(1.0f,fmaxf(-1.0f,g[p]));
}
static void require_gpu(){hipDeviceProp_t p;check(hipGetDeviceProperties(&p,0));
 if(strncmp(p.gcnArchName,"gfx1150",7)!=0)throw std::runtime_error("POCKET4_GFX1150_REQUIRED");check(hipSetDevice(0));}
extern "C" int hnl_gpu_identity(char* output,int capacity){try{
 require_gpu();hipDeviceProp_t p;check(hipGetDeviceProperties(&p,0));int runtime=0;check(hipRuntimeGetVersion(&runtime));
 if(!output||capacity<128)return -1;
 snprintf(output,capacity,"{\"device\":\"%s\",\"arch\":\"%s\",\"hip_runtime\":%d,\"backend\":\"HIP\",\"parameters\":%d,\"cpu_fallback\":false}",p.name,p.gcnArchName,runtime,P);return 0;
 }catch(const std::exception& e){if(output&&capacity>0)snprintf(output,capacity,"%s",e.what());return -1;}}
extern "C" int hnl_run(const float* x,const float* y,const float* mask,const float* teacher,
 const float* teacher_mask,float* weights,int n,int epochs,float lr,float* out_pred,
 float* out_gradient,float* loss,float* gpu_ms,char* error,int error_cap){
 try{
  if(!x||!y||!mask||!teacher||!teacher_mask||!weights||!out_pred||!out_gradient||!loss||!gpu_ms||n<1||n>4096||epochs<0||epochs>32||!std::isfinite(lr)||lr<0||lr>0.1f)throw std::runtime_error("GPU_INPUT_BOUND");
  for(int i=0;i<P;i++)if(!std::isfinite(weights[i])||fabsf(weights[i])>8)throw std::runtime_error("GPU_WEIGHT_BOUND");
  for(int i=0;i<n*F;i++)if(!std::isfinite(x[i])||fabsf(x[i])>8)throw std::runtime_error("GPU_FEATURE_BOUND");
  float denominator=0;
  for(int i=0;i<n*O;i++){
   if(!std::isfinite(y[i])||!std::isfinite(teacher[i])||fabsf(y[i])>8||fabsf(teacher[i])>8||
      (mask[i]!=0&&mask[i]!=1)||(teacher_mask[i]!=0&&teacher_mask[i]!=1))throw std::runtime_error("GPU_TARGET_BOUND");
   denominator+=mask[i];
  }
  if(epochs>0&&denominator==0)throw std::runtime_error("NO_MATURE_LABEL_NO_UPDATE");
  denominator=std::max(1.0f,denominator);require_gpu();
  Buffer dx(n*F),dy(n*O),dm(n*O),dt(n*O),dtm(n*O),dw(P),pred(n*O),ls(n*O),sg(n*P),g(P);
  check(hipMemcpy(dx.p,x,n*F*sizeof(float),hipMemcpyHostToDevice));
  check(hipMemcpy(dy.p,y,n*O*sizeof(float),hipMemcpyHostToDevice));
  check(hipMemcpy(dm.p,mask,n*O*sizeof(float),hipMemcpyHostToDevice));
  check(hipMemcpy(dt.p,teacher,n*O*sizeof(float),hipMemcpyHostToDevice));
  check(hipMemcpy(dtm.p,teacher_mask,n*O*sizeof(float),hipMemcpyHostToDevice));
  check(hipMemcpy(dw.p,weights,P*sizeof(float),hipMemcpyHostToDevice));
  hipEvent_t start,finish;check(hipEventCreate(&start));check(hipEventCreate(&finish));
  float elapsed=0;
  for(int epoch=0;epoch<=epochs;epoch++){
   check(hipEventRecord(start));
   hipLaunchKernelGGL(forward_backward,dim3((n+63)/64),dim3(64),0,0,dx.p,dy.p,dm.p,dt.p,dtm.p,dw.p,pred.p,ls.p,sg.p,n);
   hipLaunchKernelGGL(reduce_gradient,dim3((P+63)/64),dim3(64),0,0,sg.p,g.p,n,denominator);
   if(epoch<epochs)hipLaunchKernelGGL(update,dim3((P+63)/64),dim3(64),0,0,dw.p,g.p,lr);
   check(hipGetLastError());check(hipEventRecord(finish));check(hipEventSynchronize(finish));
   float t=0;check(hipEventElapsedTime(&t,start,finish));elapsed+=t;
   if(epoch<epochs)std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  check(hipEventDestroy(start));check(hipEventDestroy(finish));
  check(hipMemcpy(weights,dw.p,P*sizeof(float),hipMemcpyDeviceToHost));
  check(hipMemcpy(out_pred,pred.p,n*O*sizeof(float),hipMemcpyDeviceToHost));
  check(hipMemcpy(out_gradient,g.p,P*sizeof(float),hipMemcpyDeviceToHost));
  float* losses=new float[n*O];check(hipMemcpy(losses,ls.p,n*O*sizeof(float),hipMemcpyDeviceToHost));
  double sum=0;for(int i=0;i<n*O;i++)sum+=losses[i];delete[] losses;
  *loss=static_cast<float>(sum/denominator);*gpu_ms=elapsed;
  if(!std::isfinite(*loss))throw std::runtime_error("GPU_NONFINITE_RESULT");
  return 0;
 }catch(const std::exception& e){if(error&&error_cap>0)snprintf(error,error_cap,"%s",e.what());return -1;}
}
