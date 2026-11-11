"""Reject incomplete stress observations; build the real signed ARM64 fixtures."""
import copy,importlib.util
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
CHECK=module('stress_check',ROOT/'scripts/check-polaris-process.py')
class StressTests(unittest.TestCase):
    def test_observer_rejects_incomplete_rounds_live_children_and_unreclaimed_tasks(self):
        sample=dict(_nprocs=2,_tasks_count=2,_zombproc=0,_terminated_tasks_count=0,_terminated_threads_count=0,
                    _polaris_process_exec_checks=2,_polaris_child_signature_accepted=2)
        report=dict(stress_results=[100,1000,1000,1000,1000,301,300,300,300,300,8,4,300,32],
                    stress_samples=[dict(sample,completed=n) for n in [0,200,208,400,600,800,1000]],
                    stress_barrier=dict(sample,completed=200,width=8,_nprocs=10,_tasks_count=10),
                    snapshots=[dict(sample,_polaris_tmp_creates=1146,_polaris_tmp_unlinks=1146,_polaris_process_exec_checks=1002,_polaris_child_signature_accepted=1002)])
        self.assertTrue(CHECK.grade_stress(report))
        for field,value in [('_nprocs',3),('_tasks_count',3),('_zombproc',0x1234),('_terminated_tasks_count',1),('_terminated_threads_count',1),('_polaris_tmp_unlinks',1145),('_polaris_process_exec_checks',1001),('_polaris_child_signature_accepted',1001)]:
            bad=copy.deepcopy(report);bad['snapshots'][-1][field]=value;self.assertFalse(CHECK.grade_stress(bad),field)
        bad=copy.deepcopy(report);bad['stress_results'][2]=999;self.assertFalse(CHECK.grade_stress(bad))
        bad=copy.deepcopy(report);bad['stress_samples'].pop(2);self.assertFalse(CHECK.grade_stress(bad))
        bad=copy.deepcopy(report);bad['stress_barrier']['_nprocs']=9;self.assertFalse(CHECK.grade_stress(bad))
        bad=copy.deepcopy(report);bad['stress_samples'][3]['_zombproc']=0x1234;self.assertFalse(CHECK.grade_stress(bad))

    def test_signed_stress_bundle_has_parent_observation_points_and_tmp_enabled(self):
        builder=module('stress_bundle',ROOT/'scripts/build-polaris-process.py')
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['python3',ROOT/'scripts/build-polaris-process.py','--stress','--out',directory],check=True,capture_output=True)
            report=builder.inspect_bundle(directory)
            self.assertTrue(report['stress_enabled']);self.assertTrue(report['tmp_enabled'])
            self.assertEqual(len(report['init']['stress_symbols']),4)
            child=Path(directory,'probe.macho').read_bytes()
            self.assertIn(b'/tmp/stress0\0',child)
