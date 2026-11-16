#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <jpeglib.h>
int main(int argc,char **argv){
 if(argc!=3)return 2;
 FILE *input=fopen(argv[1],"rb");if(!input)return 1;
 struct jpeg_decompress_struct decoder;struct jpeg_error_mgr error;
 decoder.err=jpeg_std_error(&error);jpeg_create_decompress(&decoder);jpeg_stdio_src(&decoder,input);jpeg_read_header(&decoder,TRUE);
 decoder.raw_data_out=TRUE;decoder.do_fancy_upsampling=FALSE;jpeg_start_decompress(&decoder);
 if(decoder.num_components<1||decoder.num_components>3)return 3;
 JSAMPIMAGE rows=(*decoder.mem->alloc_small)((j_common_ptr)&decoder,JPOOL_IMAGE,sizeof(JSAMPARRAY)*decoder.num_components);
 unsigned char *planes[3]={0};unsigned int written[3]={0};
 for(int component=0;component<decoder.num_components;component++){
  jpeg_component_info *info=&decoder.comp_info[component];
  rows[component]=(*decoder.mem->alloc_sarray)((j_common_ptr)&decoder,JPOOL_IMAGE,info->width_in_blocks*DCTSIZE,info->v_samp_factor*DCTSIZE);
  planes[component]=calloc(info->downsampled_width,info->downsampled_height);if(!planes[component])return 4;
 }
 while(decoder.output_scanline<decoder.output_height){
  if(!jpeg_read_raw_data(&decoder,rows,decoder.max_v_samp_factor*DCTSIZE))return 5;
  for(int component=0;component<decoder.num_components;component++){
   jpeg_component_info *info=&decoder.comp_info[component];
   for(int row=0;row<info->v_samp_factor*DCTSIZE&&written[component]<info->downsampled_height;row++,written[component]++)
    for(unsigned int x=0;x<info->downsampled_width;x++)planes[component][written[component]*info->downsampled_width+x]=rows[component][row][x];
  }
 }
 printf("{\"width\":%u,\"height\":%u,\"components\":[",decoder.output_width,decoder.output_height);
 for(int component=0;component<decoder.num_components;component++){
  jpeg_component_info *info=&decoder.comp_info[component];char filename[512];snprintf(filename,sizeof(filename),"%s.%d.raw",argv[2],component);
  FILE *out=fopen(filename,"wb");if(!out)return 6;size_t bytes=info->downsampled_width*info->downsampled_height;if(fwrite(planes[component],1,bytes,out)!=bytes)return 7;fclose(out);
  printf("%s{\"width\":%u,\"height\":%u,\"h_sample\":%d,\"v_sample\":%d}",component?",":"",info->downsampled_width,info->downsampled_height,info->h_samp_factor,info->v_samp_factor);free(planes[component]);
 }
 printf("]}\n");jpeg_finish_decompress(&decoder);jpeg_destroy_decompress(&decoder);fclose(input);return 0;
}
