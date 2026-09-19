#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
int main(int argc,char **argv) {
 if(argc>1 && !strcmp(argv[1],"child")){execlp("omp","omp","update",NULL);return 111;}
 if(argc>1 && !strcmp(argv[1],"wait")){puts("waiting");fflush(stdout);for(;;)pause();}
 if(argc>1 && !strcmp(argv[1],"exit"))return 37;
 if(argc>1 && !strcmp(argv[1],"input")){int c;while((c=getchar())!=EOF)putchar(c);return 0;}
 char cwd[4096];printf("cwd=%s\n",getcwd(cwd,sizeof(cwd)));
 const char *keys[]={"HOME","PI_CONFIG_DIR","PI_CODING_AGENT_DIR","XDG_DATA_HOME","XDG_STATE_HOME","XDG_CACHE_HOME","PATH",NULL};
 for(int i=0;keys[i];i++)printf("%s=%s\n",keys[i],getenv(keys[i])?getenv(keys[i]):"");
 printf("tty=%d\n",isatty(0));
 for(int i=1;i<argc;i++)printf("arg=%s\n",argv[i]);return 0;
}
