import pytest
from scripts.reconcile_advanced_product import _application_payload, reconcile, ReconcileError


def base():
    return {'product':'hosting','repository':'goodmorning-mx/hosting','branch':'dev','topology':'resources-v3'}


@pytest.mark.parametrize('change,source',[
    ({'product':'another'}, {'branch':'feature/hosting-mvp','sha':'a'*40}),
    ({'topology':'resources-v3-production'}, {'branch':'feature/hosting-mvp','sha':'a'*40}),
    ({}, {'branch':'main','sha':'a'*40}),
    ({}, {'branch':'feature/hosting-mvp','sha':'HEAD'}),
])
def test_preview_refuses_wrong_scope_before_api(change,source):
    class NoCalls:
        apply=True
        def get(self,*args): pytest.fail('No API call allowed')
    with pytest.raises(ReconcileError,match='restricted'):
        reconcile({**base(),**change},client=NoCalls(),server_name='server',destination_uuid='destination',
                  confirm_product='hosting',confirm_environment='dev',preview_source=source)


def test_preview_payload_pins_commit_disables_auto_deploy_and_keeps_private_volume():
    app={'name':'api','role':'api','domain':'https://api.dev.hosting.goodmorning.mx',
         'base_directory':'/backend','dockerfile':'/Dockerfile','port':8000,'health_path':'/health',
         'persistent_volume':{'name':'hosting-backend-data','mount_path':'/app/data'}}
    context={k:'test' for k in ['project_uuid','environment_uuid','server_uuid','destination_uuid','github_app_uuid']}
    result=_application_payload(base(),app,context,{'branch':'feature/hosting-mvp','sha':'a'*40})
    assert result['git_branch']=='feature/hosting-mvp'
    assert result['git_commit_sha']=='a'*40
    assert result['instant_deploy'] is False
    assert result['is_auto_deploy_enabled'] is False
    assert result['environment_name']=='dev'
    assert result['custom_docker_run_options']=='--mount source=hosting-backend-data,target=/app/data'
    normal=_application_payload(base(),app,context)
    assert normal['git_branch']=='dev'
    assert normal['is_auto_deploy_enabled'] is True
    assert 'git_commit_sha' not in normal
