// Real TypeScript workflow, deterministic HTTP fixture; never contacts a server.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { runUploadedEvidenceWorkflow } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));
const wallet = '0x1111111111111111111111111111111111111111';
const data = {
  invoice: { invoice_id: 'old-request', invoice_number: 'INV-1', vendor_id: 'vendor', currency: 'USDC', amount: '70', due_date: '2026-10-08', payment_wallet_address: wallet },
  purchaseOrder: { purchase_order_id: 'po-1', po_number: 'PO-1', vendor_id: 'vendor', currency: 'USDC', authorized_amount: '70' },
  delivery: { delivery_id: 'delivery-1', purchase_order_id: 'po-1', delivered_value: '70' },
};
const files = Object.fromEntries(Object.entries(data).map(([key, value]) => [key, new File([JSON.stringify(value)], key+'.json', { type: 'application/json' })]));
const digest = async file => Buffer.from(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())).toString('hex');
const hash = await digest(files.invoice);
const original = { ...data.invoice, id: 'old-request', source_document_hash: hash, status: 'DRAFT' };
const response = (body, status=200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function fixture({ correction=false, reviewed=false, failOnce=false }={}) {
  const calls=[];
  let failed=false;
  const saved=new Map();
  const target=correction ? 'revision-server-id' : 'old-request';
  globalThis.fetch=async (path, init={}) => {
    calls.push({path, method:init.method || 'GET', body:init.body});
    if (path==='/api/vendors') return response({items:[{id:'vendor', approved_wallet_address:wallet}]});
    if (path==='/api/policies') return response({items:[{version:'finance-kept'}]});
    if (path.startsWith('/api/treasury/summary')) return response({treasury:{available_usdc:'5000'}});
    if (path==='/api/invoices/old-request') return response({invoice:{...original,status:reviewed?'REJECTED':'DRAFT'}});
    if (path==='/api/invoices') {
      assert.equal(JSON.parse(init.body).supersedes_invoice_id, 'old-request');
      return response({invoice:{...original,id:target}},201);
    }
    if (path===`/api/invoices/${target}/evidence` && init.method==='POST') {
      const kind=init.body.get('evidence_type');
      if (failOnce && !failed && kind==='PURCHASE_ORDER') {
        failed=true;
        return response({error:{message:'Temporary upload failure'}},503);
      }
      const fields=JSON.parse(init.body.get('fields'));
      if (kind==='INVOICE') assert.equal(fields.find(f=>f.name==='invoice_id').normalized_value,target);
      saved.set(kind, fields);
      return response({evidence:{id:'document-'+kind}},201);
    }
    if (path===`/api/invoices/${target}/evidence` && init.method==='GET') {
      return response({items:[{evidence_type:'INVOICE',content_sha256:hash,fields:Object.entries(data.invoice).map(([name,value])=>({name,normalized_value:value}))}]});
    }
    if (path===`/api/invoices/${target}/evaluate?auto_settle=true`) {
      assert.equal(saved.size,3,'All three attachments must be present before evaluation');
      return response({invoice:{id:target},decision:{id:'new-decision',final_action:'PAY'}});
    }
    throw new Error('Unexpected request '+path);
  };
  return {calls,saved};
}

test('partial upload retry uploads missing attachments before evaluating', async()=>{
  const {calls,saved}=fixture({failOnce:true});
  await assert.rejects(runUploadedEvidenceWorkflow('test-admin','test-operator',files), /Temporary upload failure/);
  assert.equal(saved.size,2);
  assert.equal(calls.some(c=>c.path.includes('/evaluate')),false);
  const result=await runUploadedEvidenceWorkflow('test-admin','test-operator',files);
  assert.equal(saved.size,3);
  assert.equal(result.decision.id,'new-decision');
  assert.equal(calls.some(c=>c.path.includes('?limit=50')),false);
});

test('correction uses server-generated identity and binds uploaded evidence to it', async()=>{
  const {calls}=fixture({correction:true});
  const result=await runUploadedEvidenceWorkflow('test-admin','test-operator',files,{},'simulation','old-request');
  assert.equal(result.invoice.id,'revision-server-id');
  assert.equal(calls.some(c=>c.path==='/api/invoices/old-request/evaluate?auto_settle=true'),false);
  assert.equal(calls.filter(c=>c.method==='POST' && c.path==='/api/policies').length,0);
});

test('reviewed request cannot silently return an old decision for changed supporting evidence', async()=>{
  fixture({reviewed:true});
  await assert.rejects(runUploadedEvidenceWorkflow('test-admin','test-operator',files), error=>error.code==='SEALED_EVIDENCE_CONFLICT');
});

test('same invoice id with changed amount is a conflict, not a duplicate payment', async()=>{
  const {calls}=fixture();
  await assert.rejects(runUploadedEvidenceWorkflow('test-admin','test-operator',files,{invoice:{amount:'69'}}), error=>error.code==='INVOICE_ID_CONFLICT');
  assert.equal(calls.some(c=>c.method==='POST'),false);
});
