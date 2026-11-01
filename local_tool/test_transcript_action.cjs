// Unit test the action function with plain data doubles. No browser is launched.
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const html=fs.readFileSync(path.join(__dirname,'preview.html'),'utf8');
const action=html.match(/\$\('transcript-export'\)\.onclick=(async\(\)=>\{[^\n]+\});/);
assert.ok(action,'Standalone subtitle action exists');
(async()=>{
 for(const checked of [false,true])for(const method of ['auto','speech','screen']){
  const fields={'english':{checked},'transcript-method':{value:method}},calls=[];
  const roi=[0,.7,1,1];
  const invoke=new Function('$','send','classifyPending','roi','return '+action[1]);
  const handler=invoke(id=>fields[id],(...args)=>calls.push(args),Promise.resolve(),roi);
  await handler();
  assert.equal(calls.length,1);assert.equal(calls[0][0],'transcript-export');
  assert.equal(calls[0][1].method,method);assert.deepEqual(calls[0][1].roi,method==='screen'?roi:null);
 }
 console.log('PASS: subtitles always use independent export, preserve method and ROI, with or without English classification.');
})().catch(error=>{console.error(error);process.exitCode=1;});
