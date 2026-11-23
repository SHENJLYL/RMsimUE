// Deterministic cache lifecycle/byte-equivalence contract. No real GL or solver.
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "render/classic/render_staticflex_vbo.h"
#include "render/classic/glad/glad.h"

typedef struct Buffer_ { unsigned char* bytes; size_t size; } Buffer;
static Buffer buffers[128];
static GLuint next_id=1, bound=0, vertex_buffer=0, normal_buffer=0, uv_buffer=0;
static uintptr_t vertex_offset=0, normal_offset=0, uv_offset=0;
static int upload_calls=0, delete_calls=0, fail_size=0;

static void APIENTRY genBuffers(GLsizei n, GLuint* ids) {
  for(int i=0;i<n;i++){assert(next_id<128);ids[i]=next_id++;}
}
static void APIENTRY bindBuffer(GLenum target, GLuint id) {assert(target==GL_ARRAY_BUFFER);bound=id;}
static void APIENTRY bufferData(GLenum target, GLsizeiptr size, const void* bytes, GLenum usage) {
  assert(target==GL_ARRAY_BUFFER&&usage==GL_STATIC_DRAW&&bound&&size>0);
  buffers[bound].bytes=(unsigned char*)malloc((size_t)size);assert(buffers[bound].bytes);
  memcpy(buffers[bound].bytes,bytes,(size_t)size);buffers[bound].size=(size_t)size;upload_calls++;
}
static void APIENTRY getBufferParameter(GLenum target, GLenum key, GLint* value) {
  assert(target==GL_ARRAY_BUFFER&&key==GL_BUFFER_SIZE&&bound);
  *value=fail_size?0:(GLint)buffers[bound].size;fail_size=0;
}
static void APIENTRY getInteger(GLenum key, GLint* value) {assert(key==GL_ARRAY_BUFFER_BINDING);*value=(GLint)bound;}
static void APIENTRY deleteBuffers(GLsizei n, const GLuint* ids) {
  for(int i=0;i<n;i++)if(ids[i]){assert(buffers[ids[i]].bytes);free(buffers[ids[i]].bytes);
    buffers[ids[i]].bytes=NULL;buffers[ids[i]].size=0;if(bound==ids[i])bound=0;delete_calls++;}
}
static void APIENTRY vertexPointer(GLint size, GLenum type, GLsizei stride, const void* value) {
  assert(size==3&&type==GL_FLOAT&&!stride);vertex_buffer=bound;vertex_offset=(uintptr_t)value;
}
static void APIENTRY normalPointer(GLenum type, GLsizei stride, const void* value) {
  assert(type==GL_FLOAT&&!stride);normal_buffer=bound;normal_offset=(uintptr_t)value;
}
static void APIENTRY texPointer(GLint size, GLenum type, GLsizei stride, const void* value) {
  assert(size==2&&type==GL_FLOAT&&!stride);uv_buffer=bound;uv_offset=(uintptr_t)value;
}
PFNGLGENBUFFERSPROC mjGlad_glGenBuffers=genBuffers;
PFNGLBINDBUFFERPROC mjGlad_glBindBuffer=bindBuffer;
PFNGLBUFFERDATAPROC mjGlad_glBufferData=bufferData;
PFNGLGETBUFFERPARAMETERIVPROC mjGlad_glGetBufferParameteriv=getBufferParameter;
PFNGLGETINTEGERVPROC mjGlad_glGetIntegerv=getInteger;
PFNGLDELETEBUFFERSPROC mjGlad_glDeleteBuffers=deleteBuffers;
PFNGLVERTEXPOINTERPROC mjGlad_glVertexPointer=vertexPointer;
PFNGLNORMALPOINTERPROC mjGlad_glNormalPointer=normalPointer;
PFNGLTEXCOORDPOINTERPROC mjGlad_glTexCoordPointer=texPointer;

int main(void) {
  mjModel model={0};mjrContext context={0};mjvScene scene={0};mjvGeom geom={0};
  int dim[]={2},interp[]={0},vertadr[]={0},vertnum[]={3},bodyids[]={0,0,0},texadr[]={0};
  int faceadr[]={2},facenum[]={2},faceused[]={2};
  float positions[36],normals[36],uv[24],changed[36];
  for(int i=0;i<36;i++){positions[i]=(float)i/7;normals[i]=(float)i/11;changed[i]=positions[i];}
  for(int i=0;i<24;i++)uv[i]=(float)i/13;
  model.nflex=1;model.flex_dim=dim;model.flex_interp=interp;model.flex_vertadr=vertadr;
  model.flex_vertnum=vertnum;model.flex_vertbodyid=bodyids;model.flex_texcoordadr=texadr;
  scene.nflex=1;scene.flexfaceopt=1;scene.flexfaceadr=faceadr;scene.flexfacenum=facenum;
  scene.flexfaceused=faceused;scene.flexface=positions;scene.flexnormal=normals;scene.flextexcoord=uv;
  geom.objid=0;geom.texcoord=1;geom.matid=0;

  // Default off neither registers nor uploads.
  mjr_rmsimStaticFlexInit(&model,&context);
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));assert(!upload_calls);
  rmsim_mjr_setStaticFlexCacheEnabled(1);
  mjr_rmsimStaticFlexInit(&model,&context);
  assert(mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));
  assert(upload_calls==3&&vertex_offset==0&&normal_offset==0&&uv_offset==0);
  assert(!memcmp(buffers[vertex_buffer].bytes,positions+18,18*sizeof(float)));
  assert(!memcmp(buffers[normal_buffer].bytes,normals+18,18*sizeof(float)));
  assert(!memcmp(buffers[uv_buffer].bytes,uv+12,12*sizeof(float)));
  bindBuffer(GL_ARRAY_BUFFER,0); // Same restoration performed by render_gl3.c.

  // Material slices reuse the same exact geometry and preserve byte offsets.
  assert(mjr_rmsimStaticFlexBind(&context,&scene,&geom,3,1));assert(upload_calls==3);
  assert(vertex_offset==9*sizeof(float)&&normal_offset==9*sizeof(float)&&uv_offset==6*sizeof(float));
  bindBuffer(GL_ARRAY_BUFFER,0);
  rmsim_mjr_setStaticFlexCacheEnabled(0);
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));assert(upload_calls==3);
  rmsim_mjr_setStaticFlexCacheEnabled(1);
  assert(mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));assert(upload_calls==3);
  bindBuffer(GL_ARRAY_BUFFER,0);

  // A new scene buffer invalidates this context conservatively and frees VBOs.
  scene.flexface=changed;
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));assert(delete_calls==3);
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));assert(upload_calls==3);
  mjr_rmsimStaticFlexFree(&context);mjr_rmsimStaticFlexFree(&context);
  assert(delete_calls==3);
  assert(strstr(rmsim_mjr_staticFlexCacheStatus(),"\"live_contexts\":0"));
  assert(strstr(rmsim_mjr_staticFlexCacheStatus(),"\"live_bytes\":0"));

  // Dynamic vertices, interpolated flex, and non-2D geometry are rejected.
  bodyids[1]=1;mjr_rmsimStaticFlexInit(&model,&context);
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));bodyids[1]=0;
  interp[0]=1;mjr_rmsimStaticFlexInit(&model,&context);
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));interp[0]=0;
  dim[0]=3;mjr_rmsimStaticFlexInit(&model,&context);
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));dim[0]=2;
  assert(upload_calls==3);

  // Failed GPU allocation leaves no live partial buffers and uses fallback.
  scene.flexface=positions;texadr[0]=-1;geom.texcoord=0;
  mjr_rmsimStaticFlexInit(&model,&context);fail_size=1;
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));
  assert(delete_calls==4&&bound==0);mjr_rmsimStaticFlexFree(&context);

  // Invalid face slice is rejected before upload; supported fresh context works.
  mjr_rmsimStaticFlexInit(&model,&context);
  assert(!mjr_rmsimStaticFlexBind(&context,&scene,&geom,3,2));
  mjr_rmsimStaticFlexFree(&context);
  mjr_rmsimStaticFlexInit(&model,&context);
  assert(mjr_rmsimStaticFlexBind(&context,&scene,&geom,2,2));assert(upload_calls==6);
  bindBuffer(GL_ARRAY_BUFFER,0);mjr_rmsimStaticFlexFree(&context);
  assert(delete_calls==6);
  assert(strstr(rmsim_mjr_staticFlexCacheStatus(),"\"live_flex\":0"));
  assert(strstr(rmsim_mjr_staticFlexCacheStatus(),"\"live_bytes\":0"));
  puts("PASS: default-off, full bytes/UV, slices, reuse, bypass, scene invalidation, dynamic rejection, allocation fallback, lifecycle");
  puts(rmsim_mjr_staticFlexCacheStatus());
  return 0;
}
