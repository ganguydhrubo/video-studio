const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('video_studio/studio_ui/index.html','utf8');
assert.match(html,/<input type="file" id="fileInput" class="file-chooser"/);
const script=html.split('<script>')[1].split('</script>')[0];
new vm.Script(script);
const uploader=script.split('/* uploads */')[1].split('/* submit */')[0];
const elements=new Map(),requests=[];
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',dataset:{},style:{},classList:{add(){},remove(){}},addEventListener(){},querySelectorAll(){return[]},removeAttribute(){},load(){}});return elements.get(id)};
class XHR{open(method,path){this.path=path;requests.push(path)}setRequestHeader(){}upload={};send(file){this.sent=file}}
const context=vm.createContext({$,S:{files:[]},TOKEN:'test',XMLHttpRequest:XHR,URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},World:{pull(){}},esc:String,media:p=>p,fmtTime:String,toast(){},api(){throw new Error('No HTTP request may open the file dialog')}});
vm.runInContext(uploader,context);
assert.equal(requests.length,0);
assert.equal($('#drop').onclick,undefined);
const field=$('#fileInput');field.value='C:\\fakepath\\clip.mov';
field.onchange({target:Object.assign(field,{files:[{name:'clip.mov',type:'video/quicktime',size:113*1024*1024}]})});
assert.deepEqual(requests,['/api/upload']);
assert.equal(field.value,'');
assert.equal(context.S.files.length,1);
console.log('File input passed: native browser control, no dialog RPC, one streamed copy after selection, repeat selection enabled.');
