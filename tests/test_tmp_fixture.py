"""Exercise actual kernel tmp transfer/resize routines with faulting user I/O."""
import importlib.util
from pathlib import Path
import shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]

def routine(text,name):
    start=text.index('static int '+name+'(');body=text.index('{',start);depth=1;end=body+1
    while depth:
        if text[end]=='{':depth+=1
        elif text[end]=='}':depth-=1
        end+=1
    return text[start:end]

class TmpTests(unittest.TestCase):
    def test_signed_tmp_fixture_is_distinct_and_bounded(self):
        spec=importlib.util.spec_from_file_location('bundle',ROOT/'scripts/build-polaris-process.py')
        bundle=importlib.util.module_from_spec(spec);spec.loader.exec_module(bundle)
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(['python3',ROOT/'scripts/build-polaris-process.py','--tmp','--out',d],check=True,capture_output=True)
            report=bundle.inspect_bundle(d)
            self.assertTrue(report['tmp_enabled']);self.assertEqual(len(report['init']['tmp_symbols']),2)
            self.assertEqual(len(report['entries']),10)
            self.assertNotEqual(report['init']['cdhash'],report['child']['cdhash'])

    def test_actual_io_resize_fault_atomicity_and_pool_identity(self):
        patch=(ROOT/'kernel-overrides/xnu/polaris-tmp.patch').read_text()
        text='\n'.join(l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++'))
        actual='\n'.join(routine(text,n) for n in ['polaris_tmp_name','polaris_tmp_reusable','polaris_tmp_io','polaris_tmp_setattr'])
        harness=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <errno.h>
#include <sys/types.h>
typedef int64_t user_ssize_t;typedef size_t user_size_t;typedef char *caddr_t;
struct node {unsigned char polaris_bytes[4096];uint64_t size;int polaris_data_mtx,polaris_linked,polaris_pins,polaris_creating,polaris_dynamic;void *vp;};
typedef struct node *mockfs_fsnode_t;typedef struct node *vnode_t;
struct uio {off_t offset;user_ssize_t resid;int writing,fault;unsigned char *user;};typedef struct uio *uio_t;
struct vnode_attr {uint64_t va_data_size;int active,supported;};
struct vnop_setattr_args {vnode_t a_vp;struct vnode_attr *a_vap;};
#define VATTR_IS_ACTIVE(vap,name) ((vap)->active)
#define VATTR_SET_SUPPORTED(vap,name) ((vap)->supported=1)
#define Z_WAITOK 0
#define bzero(p,n) memset(p,0,n)
static uint64_t polaris_tmp_reads,polaris_tmp_writes;
static int locked,alloc_fail,duplicate_fail,duplicates;static size_t allocated;
static mockfs_fsnode_t vnode_fsnode(vnode_t n){return n;}
static off_t uio_offset(uio_t u){return u->offset;}
static user_ssize_t uio_resid(uio_t u){return u->resid;}
static void uio_setoffset(uio_t u,off_t o){u->offset=o;}
static void uio_update(uio_t u,user_size_t n){assert((uint64_t)u->resid>=n);u->resid-=n;u->offset+=n;u->user+=n;}
static void lck_mtx_lock(int *m){assert(!locked&&!*m);*m=locked=1;}
static void lck_mtx_unlock(int *m){assert(locked&&*m);*m=locked=0;}
static void ubc_setsize(vnode_t n,off_t size){assert(locked&&n->size==(uint64_t)size);}
static void *kalloc_data(size_t n,int flags){assert(!locked&&!allocated&&n>0&&n<=4096&&flags==0);if(alloc_fail)return NULL;allocated=n;return malloc(n);}
static void kfree_data(void *p,size_t n){assert(!locked&&allocated==n);free(p);allocated=0;}
static uio_t uio_duplicate(uio_t u){assert(!locked);if(duplicate_fail)return NULL;uio_t v=malloc(sizeof(*v));*v=*u;duplicates++;return v;}
static void uio_free(uio_t u){duplicates--;free(u);}
static int uiomove(caddr_t p,int n,uio_t u){assert(!locked&&n>=0&&n<=u->resid);if(u->fault){if(n)uio_update(u,1);return EFAULT;}if(u->writing)memcpy(p,u->user,n);else memcpy(u->user,p,n);uio_update(u,n);return 0;}
'''+actual+r'''
int main(void){
 struct node n={.polaris_dynamic=1};unsigned char input[4096],out[4096];memset(input,0x5a,sizeof(input));
 assert(polaris_tmp_name("valid-1.txt",11)&&!polaris_tmp_name(".",1)&&!polaris_tmp_name("..",2)&&!polaris_tmp_name("a/b",3)&&!polaris_tmp_name("",0));
 assert(polaris_tmp_reusable(&n));n.polaris_linked=1;assert(!polaris_tmp_reusable(&n));n.polaris_linked=0;n.vp=&n;assert(!polaris_tmp_reusable(&n));n.vp=NULL;n.polaris_pins=1;assert(!polaris_tmp_reusable(&n));n.polaris_pins=0;n.polaris_creating=1;assert(!polaris_tmp_reusable(&n));n.polaris_creating=0;
 struct uio u={8,2,1,0,input};assert(!polaris_tmp_io(&n,&u,1,0)&&n.size==10&&u.offset==10&&!u.resid);assert(!n.polaris_bytes[7]&&n.polaris_bytes[8]==0x5a);
 u=(struct uio){0,1,1,0,input};assert(!polaris_tmp_io(&n,&u,1,1)&&n.size==11&&u.offset==11);
 u=(struct uio){9,4096,0,0,out};assert(!polaris_tmp_io(&n,&u,0,0)&&u.offset==11&&u.resid==4094);
 u=(struct uio){11,1,0,0,out};assert(!polaris_tmp_io(&n,&u,0,0)&&u.resid==1);
 unsigned char saved[4096];memcpy(saved,n.polaris_bytes,4096);
 u=(struct uio){4095,2,1,0,input};assert(polaris_tmp_io(&n,&u,1,0)==ENOSPC&&u.offset==4095&&u.resid==2&&!memcmp(saved,n.polaris_bytes,4096));
 u=(struct uio){1,2,1,1,input};assert(polaris_tmp_io(&n,&u,1,0)==EFAULT&&u.offset==1&&u.resid==2&&n.size==11&&!memcmp(saved,n.polaris_bytes,4096));
 alloc_fail=1;u=(struct uio){0,1,1,0,input};assert(polaris_tmp_io(&n,&u,1,0)==ENOMEM);alloc_fail=0;
 duplicate_fail=1;assert(polaris_tmp_io(&n,&u,1,0)==ENOMEM);duplicate_fail=0;
 u=(struct uio){-1,1,0,0,out};assert(polaris_tmp_io(&n,&u,0,0)==EINVAL);
 u=(struct uio){0,INT64_MAX,1,0,input};assert(polaris_tmp_io(&n,&u,1,0)==ENOSPC);
 struct vnode_attr attr={.va_data_size=4,.active=1};struct vnop_setattr_args ap={&n,&attr};
 assert(!polaris_tmp_setattr(&ap)&&attr.supported&&n.size==4);attr.va_data_size=16;assert(!polaris_tmp_setattr(&ap)&&n.size==16);for(unsigned i=4;i<16;i++)assert(!n.polaris_bytes[i]);
 attr.va_data_size=4097;assert(polaris_tmp_setattr(&ap)==EFBIG&&n.size==16);
 u=(struct uio){0,4096,1,0,input};assert(!polaris_tmp_io(&n,&u,1,0)&&n.size==4096);
 u=(struct uio){0,1,1,0,input};assert(polaris_tmp_io(&n,&u,1,1)==ENOSPC&&u.offset==0&&u.resid==1);
 assert(!allocated&&!duplicates&&!locked&&polaris_tmp_writes==3&&polaris_tmp_reads==1);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);(d/'tmp.c').write_text(harness)
            compiled=subprocess.run([shutil.which('clang'),'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',d/'tmp.c','-o',d/'tmp'],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            subprocess.run([d/'tmp'],check=True,capture_output=True)
