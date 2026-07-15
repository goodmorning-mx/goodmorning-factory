import argparse, os, unittest
from unittest import mock
import scripts.provision_product_development as pp


def args(**o):
    b=dict(product='ledger',repository='goodmorning-mx/ledger',branch='dev',coolify_project='ledger',environment='dev',server='development',github_app='gm-app',dry_run=True,apply=False,confirm_product=None)
    b.update(o); return argparse.Namespace(**b)

class FakeClient:
    def __init__(self,payloads): self.payloads=payloads; self.calls=[]; self.mode=None
    def get(self,path):
        self.calls.append(('GET',path))
        v=self.payloads.get(path,[])
        if isinstance(v,Exception): raise v
        return v
    def create_project(self,payload): self.calls.append(('POST','/projects',pp.redact(payload))); return pp.Response(201,{'uuid':'project-1','name':payload['name']},{})
    def create_environment(self,project_uuid,payload): self.calls.append(('POST',f'/projects/{project_uuid}/environments',payload)); return pp.Response(201,{'uuid':'env-1','name':'dev'},{})
    def create_postgresql(self,payload): self.calls.append(('POST','/databases/postgresql',pp.redact(payload))); return pp.Response(201,{'uuid':'db-1','internal_db_url':'postgres://fake:fake@fake/db'},{})


def fixtures(**o):
    p={'/servers':[{'uuid':'server-1','name':'development'}],'/github-apps':{'github_apps':[{'uuid':'app-1','name':'gm-app'}]},'/projects':[{'uuid':'project-1','name':'ledger'}],'/projects/project-1':{'uuid':'project-1','name':'ledger','environments':[{'uuid':'env-1','name':'dev'}]},'/databases':[{'uuid':'db-1','name':'ledger-postgres-dev','database_type':'standalone-postgresql','image':'postgres:16-alpine','is_public':False,'project_uuid':'project-1','environment_uuid':'env-1','server_uuid':'server-1','postgres_user':'ledger_dev_user'}],'/databases/db-1':{'uuid':'db-1','name':'ledger-postgres-dev','database_type':'standalone-postgresql','image':'postgres:16-alpine','is_public':False,'project_uuid':'project-1','environment_uuid':'env-1','server_uuid':'server-1','postgres_user':'ledger_dev_user','internal_db_url':'postgres://fake:fake@fake/db'},'/applications':[]}
    p.update(o); return p

class ProvisionerStage3(unittest.TestCase):
    def test_dry_run_existing_compatible_uses_get_only(self):
        c=FakeClient(fixtures())
        r=pp.provision(args(),client_factory=lambda:c)
        self.assertEqual(r['status'],'dry_run'); self.assertEqual(r['postgresql']['status'],'exists')
        self.assertFalse(any(x[0]=='POST' for x in c.calls))
        self.assertTrue(r['postgresql']['internal_db_url_available'])
        self.assertNotIn('fake:fake', str(r))

    def test_dry_run_all_absent_plans_symbolic(self):
        c=FakeClient(fixtures(**{'/projects':[], '/databases':[]}))
        r=pp.provision(args(),client_factory=lambda:c)
        self.assertEqual(r['project']['status'],'would_create')
        self.assertEqual(r['environment_resource']['status'],'would_create')
        self.assertEqual(r['postgresql']['status'],'would_create')
        self.assertFalse(any(x[0]=='POST' for x in c.calls))

    def test_project_exists_dev_absent_and_only_production_plans_dev(self):
        c=FakeClient(fixtures(**{'/projects/project-1':{'environments':[{'uuid':'prod','name':'production'}]},'/projects/project-1/environments':[{'uuid':'prod','name':'production'}],'/databases':[]}))
        r=pp.provision(args(),client_factory=lambda:c)
        self.assertEqual(r['environment_resource']['status'],'would_create')
        self.assertNotEqual(r['environment_resource'].get('uuid'),'prod')

    def test_postgres_incompatible_and_out_of_scope(self):
        c=FakeClient(fixtures(**{'/databases':[{'uuid':'db-1','name':'ledger-postgres-dev','database_type':'standalone-postgresql','image':'postgres:15','is_public':False,'project_uuid':'project-1','environment_uuid':'env-1','server_uuid':'server-1'}],'/databases/db-1':{'uuid':'db-1','name':'ledger-postgres-dev','database_type':'standalone-postgresql','image':'postgres:15','is_public':False,'project_uuid':'project-1','environment_uuid':'env-1','server_uuid':'server-1'}}))
        self.assertEqual(pp.provision(args(),client_factory=lambda:c)['status'],'recreate_required')
        c=FakeClient(fixtures(**{'/databases':[{'uuid':'db-1','name':'ledger-postgres-dev','database_type':'standalone-postgresql','image':'postgres:16','is_public':False,'project_uuid':'other','environment_uuid':'env-1','server_uuid':'server-1'}],'/databases/db-1':{'uuid':'db-1','name':'ledger-postgres-dev','database_type':'standalone-postgresql','image':'postgres:16','is_public':False,'project_uuid':'other','environment_uuid':'env-1','server_uuid':'server-1'}}))
        self.assertEqual(pp.provision(args(),client_factory=lambda:c)['postgresql']['status'],'out_of_scope')

    def test_duplicates_fail(self):
        with self.assertRaisesRegex(pp.ProvisioningError,'multiple Coolify project'):
            pp.provision(args(),client_factory=lambda:FakeClient(fixtures(**{'/projects':[{'name':'ledger','uuid':'1'},{'name':'ledger','uuid':'2'}]})))
        with self.assertRaisesRegex(pp.ProvisioningError,'multiple PostgreSQL'):
            pp.provision(args(),client_factory=lambda:FakeClient(fixtures(**{'/databases':[{'name':'ledger-postgres-dev','uuid':'1'},{'name':'ledger-postgres-dev','uuid':'2'}]})))

    def test_legacy_compose_reported(self):
        c=FakeClient(fixtures(**{'/applications':[{'uuid':'old','name':'ledger-dev','build_pack':'dockercompose'}]}))
        r=pp.provision(args(),client_factory=lambda:c)
        self.assertEqual(r['legacy_compose'][0]['name'],'ledger-dev')

    def test_apply_gates_and_client_safety(self):
        mode=pp.ExecutionMode('dry_run','ledger',None,False); client=pp.CoolifyClient('https://coolify.example','token',mode=mode)
        for method in ('POST','PATCH','PUT','DELETE'):
            with self.assertRaises(pp.ProvisioningError): client.request(method,'/projects')
        for path in ('/deploy','/applications/x/start','/databases/x/restart','/databases/x/stop'):
            with self.assertRaises(pp.ProvisioningError): client.request('GET',path)
        for m in [pp.ExecutionMode('apply','ledger',None,True), pp.ExecutionMode('apply','ledger','other',True), pp.ExecutionMode('apply','ledger','ledger',False)]:
            client=pp.CoolifyClient('https://coolify.example','token',mode=m)
            with self.assertRaises(pp.ProvisioningError): client.create_project({'name':'ledger'})
        client=pp.CoolifyClient('https://coolify.example','token',mode=pp.ExecutionMode('apply','ledger','ledger',True))
        with self.assertRaisesRegex(pp.ProvisioningError,'Application'): client.request('POST','/applications',post_kind='project_create')
        with self.assertRaisesRegex(pp.ProvisioningError,'allowlisted'): client.request('POST','/databases/mysql',post_kind='postgresql_create')

    @mock.patch.dict(os.environ, {'COOLIFY_ALLOW_RESOURCE_WRITES':'true'})
    @mock.patch('scripts.provision_product_development.generate_postgres_password', return_value='FAKE_TEST_PASSWORD_MARKER')
    def test_apply_creates_project_dev_postgres_without_deploy(self,_):
        c=FakeClient(fixtures(**{'/projects':[],'/databases':[], '/databases/db-1':{'uuid':'db-1','name':'ledger-postgres-dev','database_type':'standalone-postgresql','image':'postgres:16-alpine','is_public':False,'project_uuid':'project-1','environment_uuid':'env-1','server_uuid':'server-1','postgres_user':'ledger_dev_user','internal_db_url':'postgres://fake:fake@fake/db'}}))
        r=pp.provision(args(dry_run=False,apply=True,confirm_product='ledger'),client_factory=lambda:c)
        self.assertEqual(r['status'],'created')
        self.assertIn(('POST','/projects',{'name':'ledger'}), c.calls)
        self.assertFalse(any('deploy' in call[1] or 'start' in call[1] for call in c.calls))
        self.assertNotIn('FAKE_TEST_PASSWORD_MARKER', str(r)); self.assertNotIn('fake:fake', str(r))

    def test_apply_without_env_gate_or_confirm_fails_before_post(self):
        for kw in ({'confirm_product':'ledger'}, {'confirm_product':None}, {'confirm_product':'other'}):
            c=FakeClient(fixtures(**{'/projects':[]}))
            with self.assertRaises(pp.ProvisioningError): pp.provision(args(dry_run=False,apply=True,**kw),client_factory=lambda:c)
            self.assertFalse(any(x[0]=='POST' for x in c.calls))

    def test_malformed_creation_after_project_is_partial(self):
        class Bad(FakeClient):
            def create_environment(self,project_uuid,payload): self.calls.append(('POST','env',payload)); raise pp.ProvisioningError('boom password=FAKE', error_type='coolify_api')
        with mock.patch.dict(os.environ, {'COOLIFY_ALLOW_RESOURCE_WRITES':'true'}):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product='ledger'),client_factory=lambda:Bad(fixtures(**{'/projects':[], '/projects/project-1':{}, '/projects/project-1/environments':[]})))
        self.assertEqual(r['status'],'partial'); self.assertNotIn('FAKE', str(r))

    def test_redaction(self):
        msg=pp.safe_http_body('{"postgres_password":"FAKEPASS","internal_db_url":"postgres://u:p@h/db","DATABASE_URL":"postgresql://u:p@h/db","nested":{"Authorization":"Bearer FAKEBEARER"}}')
        for s in ('FAKEPASS','u:p','FAKEBEARER'): self.assertNotIn(s,msg)
        e=pp.ProvisioningError('Authorization: Bearer FAKE token=NOPE password=NO')
        self.assertNotIn('FAKE', e.message); self.assertNotIn('NOPE', e.message)

    def test_sql_names_are_deterministic_safe(self):
        self.assertEqual(pp.deterministic_sql_names('my-product')['postgres_db'],'my_product_dev')

if __name__=='__main__': unittest.main()
