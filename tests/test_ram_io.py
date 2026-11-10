"""Exercise the real kernel RAM-file transfer routine with bounded fake uio."""
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]


class RamIoTests(unittest.TestCase):
    def test_actual_transfer_routine_bounds_faults_and_copy_direction(self):
        patch=(ROOT/'kernel-overrides/xnu/polaris-io.patch').read_text()
        added='\n'.join(l[1:] for l in patch.splitlines() if l.startswith(('+',' ')) and not l.startswith('+++'))
        start=added.index('static int\npolaris_mockfs_io(');body=added.index('{',start);depth=1;end=body+1
        while depth:
            if added[end]=='{':depth+=1
            elif added[end]=='}':depth-=1
            end+=1
        routine=added[start:end]
        harness=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <sys/types.h>
typedef int64_t user_ssize_t;
typedef char *caddr_t;
#define Z_WAITOK 0
struct mount { void *mnt_data; };
struct node { struct mount *mnt; unsigned char polaris_bytes[4096]; };
typedef struct node *mockfs_fsnode_t;
typedef struct node *vnode_t;
struct mockmount { int mockfs_mnt_mtx; };
typedef struct mockmount *mockfs_mount_t;
struct uio { off_t offset; user_ssize_t resid; int writing,fault; unsigned char *user; };
static uint64_t polaris_io_file_reads,polaris_io_file_writes;
static int locked,alloc_fail;static size_t allocated;
static void *vnode_fsnode(vnode_t vp){return vp;}
static off_t uio_offset(struct uio *u){return u->offset;}
static user_ssize_t uio_resid(struct uio *u){return u->resid;}
static void lck_mtx_lock(int *m){assert(!locked && !*m);*m=1;locked=1;}
static void lck_mtx_unlock(int *m){assert(locked && *m);*m=0;locked=0;}
static void *kalloc_data(size_t n,int flags){assert(n>0&&n<=4096&&flags==0&&!allocated);if(alloc_fail)return NULL;allocated=n;return malloc(n);}
static void kfree_data(void *p,size_t n){assert(n==allocated);free(p);allocated=0;}
static int uiomove(caddr_t p,int n,struct uio*u){assert(!locked&&n>=0&&n<=u->resid);if(u->fault)return EFAULT;if(u->writing)memcpy(p,u->user,(size_t)n);else memcpy(u->user,p,(size_t)n);u->offset+=n;u->resid-=n;return 0;}
'''+routine+r'''
int main(void){
 struct mockmount mm={0};struct mount mp={&mm};struct node node={.mnt=&mp};
 unsigned char bytes[4096],out[4096];for(unsigned i=0;i<4096;i++)bytes[i]=(unsigned char)i;
 struct uio u={0,4096,1,0,bytes};assert(polaris_mockfs_io(&node,&u,1)==0);assert(u.offset==4096&&u.resid==0);assert(!memcmp(node.polaris_bytes,bytes,4096));
 u=(struct uio){4095,8,0,0,out};assert(polaris_mockfs_io(&node,&u,0)==0);assert(u.resid==7&&u.offset==4096&&out[0]==255);
 u=(struct uio){4095,2,1,0,bytes};assert(polaris_mockfs_io(&node,&u,1)==ENOSPC);assert(u.offset==4095&&u.resid==2&&!memcmp(node.polaris_bytes,bytes,4096));
 u=(struct uio){4096,1,1,0,bytes};assert(polaris_mockfs_io(&node,&u,1)==ENOSPC);
 u=(struct uio){4096,1,0,0,out};assert(polaris_mockfs_io(&node,&u,0)==0&&u.resid==1);
 u=(struct uio){-1,1,1,0,bytes};assert(polaris_mockfs_io(&node,&u,1)==EINVAL);
 u=(struct uio){1,INT64_MAX,1,0,bytes};assert(polaris_mockfs_io(&node,&u,1)==ENOSPC);
 u=(struct uio){0,-1,0,0,out};assert(polaris_mockfs_io(&node,&u,0)==EINVAL);
 u=(struct uio){4096,0,1,0,bytes};assert(polaris_mockfs_io(&node,&u,1)==0);
 unsigned char before[4096];memcpy(before,node.polaris_bytes,4096);
 u=(struct uio){8,8,1,1,bytes};assert(polaris_mockfs_io(&node,&u,1)==EFAULT);assert(!memcmp(before,node.polaris_bytes,4096));
 alloc_fail=1;u=(struct uio){0,1,1,0,bytes};assert(polaris_mockfs_io(&node,&u,1)==ENOMEM);alloc_fail=0;
 assert(!allocated&&!locked&&polaris_io_file_writes==1&&polaris_io_file_reads==1);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            work=Path(temporary);(work/'ram.c').write_text(harness)
            compiled=subprocess.run([shutil.which('clang'),'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',work/'ram.c','-o',work/'ram'],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            subprocess.run([work/'ram'],check=True,capture_output=True)
