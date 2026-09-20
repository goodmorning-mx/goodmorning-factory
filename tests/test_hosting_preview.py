import unittest
from scripts.reconcile_advanced_product import _application_payload, reconcile, ReconcileError


def base():
    return {'product':'hosting','repository':'goodmorning-mx/hosting','branch':'dev','topology':'resources-v3'}


class HostingPreviewTests(unittest.TestCase):
    def test_preview_refuses_wrong_scope_before_api(self):
        class NoCalls:
            apply=True
            def get(self,*args): raise AssertionError('No API call allowed')
        cases=[({'product':'another'}, {'branch':'feature/hosting-mvp','sha':'a'*40}),
               ({'topology':'resources-v3-production'}, {'branch':'feature/hosting-mvp','sha':'a'*40}),
               ({}, {'branch':'main','sha':'a'*40}),
               ({}, {'branch':'feature/hosting-mvp','sha':'HEAD'})]
        for change,source in cases:
            with self.subTest(change=change,source=source), self.assertRaisesRegex(ReconcileError,'restricted'):
                reconcile({**base(),**change},client=NoCalls(),server_name='server',destination_uuid='destination',
                          confirm_product='hosting',confirm_environment='dev',preview_source=source)

    def test_preview_payload_pins_commit_disables_auto_deploy_and_keeps_private_volume(self):
        app={'name':'api','role':'api','domain':'https://api.dev.hosting.goodmorning.mx',
             'base_directory':'/backend','dockerfile':'/Dockerfile','port':8000,'health_path':'/health',
             'persistent_volume':{'name':'hosting-backend-data','mount_path':'/app/data'}}
        context={k:'test' for k in ['project_uuid','environment_uuid','server_uuid','destination_uuid','github_app_uuid']}
        result=_application_payload(base(),app,context,{'branch':'feature/hosting-mvp','sha':'a'*40})
        self.assertEqual(result['git_branch'],'feature/hosting-mvp')
        self.assertEqual(result['git_commit_sha'],'a'*40)
        self.assertFalse(result['instant_deploy'])
        self.assertFalse(result['is_auto_deploy_enabled'])
        self.assertEqual(result['environment_name'],'dev')
        self.assertEqual(result['custom_docker_run_options'],'--mount source=hosting-backend-data,target=/app/data')
        normal=_application_payload(base(),app,context)
        self.assertEqual(normal['git_branch'],'dev')
        self.assertTrue(normal['is_auto_deploy_enabled'])
        self.assertNotIn('git_commit_sha',normal)
