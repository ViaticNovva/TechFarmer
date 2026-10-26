// Offline DOM/launcher static checks; not a native Windows, browser or OAuth execution.
"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const root=path.join(__dirname,".."),html=fs.readFileSync(path.join(root,"START-HERE.html"),"utf8");
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(scripts.length,1);
assert(!/<(?:script|link|img|iframe)[^>]*(?:src|href)\s*=\s*["']https?:/i.test(html));
assert(!/\b(?:fetch\s*\(|XMLHttpRequest|WebSocket|localStorage|sessionStorage)\b/.test(scripts[0][1]));
assert(/connect-src 'none'/.test(html));
assert(!/type=["']password/i.test(html));
assert(!/discord\.gg\//i.test(html));
const nodes=new Map(),opened=[],downloads=[];let downloadedText;
class Node {constructor(id,value=""){this.id=id;this.value=value;this.checked=false;this.disabled=true;this.textContent="";this.events={};}addEventListener(n,h){this.events[n]=h;}click(){if(this.events.click)this.events.click();else downloads.push({name:this.download,text:downloadedText});}remove(){}}
for(const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)){const value=/\bvalue="([^"]*)"/.exec(match[0]);nodes.set(match[1],new Node(match[1],value?value[1]:""));}
nodes.get("device").value="cpu";
class BlobDouble{constructor(parts){downloadedText=parts.join("");}}
const URLDouble=class extends URL{};URLDouble.createObjectURL=()=>"blob:offline-fixture";URLDouble.revokeObjectURL=()=>{};
const context=vm.createContext({document:{getElementById:id=>nodes.get(id),createElement:id=>new Node(id),body:{appendChild:()=>{}}},window:{open:(...args)=>opened.push(args)},URL:URLDouble,URLSearchParams,Blob:BlobDouble,setTimeout:fn=>fn()});
vm.runInContext(scripts[0][1]+"\nglobalThis.api=TechFarmerSetup;",context);
const api=context.api;
for(const value of ["","1","00000000000000001"," 12345678901234567","12345678901234567 ","1e18","１２３４５６７８９０１２３４５６７","18446744073709551616","https://discord.gg/fixture","not.a.token"]){assert.equal(api.validId(value),false,value);}
assert.equal(api.validId("18446744073709551615"),true);
for(const id of ["application-id","owner-id","guild-id"]){assert.equal(nodes.get(id).value,"");}
nodes.get("add-bot").click();nodes.get("save-settings").click();nodes.get("save-source").click();
assert.equal(opened.length,0);assert.equal(downloads.length,0);
// Synthetic IDs only, never real user/application identifiers.
const app="123456789012345678",owner="234567890123456789",guild="345678901234567890";
nodes.get("application-id").value=app;nodes.get("owner-id").value=owner;nodes.get("guild-id").value=guild;nodes.get("guild-id").events.input();
assert.equal(nodes.get("add-bot").disabled,false);assert.equal(nodes.get("save-settings").disabled,false);
assert.equal(opened.length,0);
const config=api.settings(app,owner,guild),url=new URL(api.authorization(config));
assert.equal(config.schema_version,3);assert.equal(config.device,"cpu");assert.equal(config.model_cache,".cache/models");assert.equal(config.storage_channel_id,null);
assert.equal(api.settings(app,owner,guild,{storage_channel_id:app}).storage_channel_id,app);
assert.throws(()=>api.settings(app,owner,guild,{storage_channel_id:"not-an-id"}));
assert(html.includes("/farmer pull url:"));assert(html.includes("mode:collection"));assert(html.includes("UPDATE-WINDOWS.cmd"));assert(html.includes("First upgrade"));
assert.equal(url.origin,"https://discord.com");assert.equal(url.pathname,"/oauth2/authorize");
assert.equal(url.searchParams.get("scope"),"bot applications.commands");assert.equal(url.searchParams.get("permissions"),"35840");
assert.equal(Number(url.searchParams.get("permissions"))&(8+32),0);assert.equal(url.searchParams.get("guild_id"),guild);assert.equal(url.searchParams.get("disable_guild_select"),"true");
nodes.get("add-bot").click();assert.equal(opened.length,1);assert.equal(opened[0][0],url.href);assert.equal(opened[0][2],"noopener,noreferrer");assert(nodes.get("validation").textContent.includes("not verified"));
nodes.get("save-settings").click();assert.equal(downloads[0].name,"tech-farmer-settings.json");const saved=JSON.parse(downloads[0].text);assert.equal(saved.owner_id,owner);assert.equal(saved.max_tiles,24);assert(!/token|password|secret/i.test(downloads[0].text));
for(const options of [{model_cache:"../escape"},{model_cache:"/root"},{device:"cloud"},{pages_per_run:0},{pages_per_run:1.5},{storage_mib:2}])assert.throws(()=>api.settings(app,owner,guild,options));
nodes.get("model-cache").value="../escape";nodes.get("model-cache").events.input();assert.equal(nodes.get("save-settings").disabled,true);nodes.get("model-cache").value=".cache/models";nodes.get("model-cache").events.input();
assert.throws(()=>api.sourceProfile("https://fixture.example","","/","/",".reader img",".chapters a",false));
for(const origin of ["http://fixture.example","https://user:pass@fixture.example","https://fixture.example/private","https://fixture.example?token=x","https://127.0.0.1"]){assert.throws(()=>api.sourceProfile(origin,"","/","/",".reader img",".chapters a",true));}
nodes.get("source-origin").value="https://fixture.example";nodes.get("rights").checked=true;nodes.get("rights").events.input();assert.equal(nodes.get("save-source").disabled,false);
nodes.get("save-source").click();assert.equal(downloads[1].name,"source-profiles.json");const source=JSON.parse(downloads[1].text);assert.equal(source.authorized,true);assert.equal(source.allow_http,false);assert.deepEqual(source.profiles[0].page_origins,["https://fixture.example"]);assert.equal(opened.length,1);
for(const name of ["INSTALL-WINDOWS.cmd","INSTALL-VISION-WINDOWS.cmd","DOWNLOAD-MODEL-WINDOWS.cmd","START-WINDOWS.cmd","UPDATE-WINDOWS.cmd"]){const text=fs.readFileSync(path.join(root,name),"utf8");assert(text.includes('cd /d "%~dp0"'));assert(!/--index-url|--extra-index-url|--trusted-host|ExecutionPolicy|runas|netsh|setx\s+/i.test(text));assert(text.includes("if errorlevel")||name==="START-WINDOWS.cmd");assert(!/\r/.test(text));}
assert(fs.readFileSync(path.join(root,"requirements-vision.txt"),"utf8").includes("transformers==4.57.6"));
for(const name of ["INSTALL-WINDOWS.cmd","INSTALL-VISION-WINDOWS.cmd","DOWNLOAD-MODEL-WINDOWS.cmd"]){assert(fs.readFileSync(path.join(root,name),"utf8").includes('call "%~dp0START-WINDOWS.cmd"'));}
const launcher=fs.readFileSync(path.join(root,"START-WINDOWS.cmd"),"utf8");
assert(launcher.includes('"%~dp0tech_farmer\\__main__.py" menu'));assert(launcher.includes("DisableDelayedExpansion"));
assert(launcher.indexOf("py -3.15")<launcher.indexOf("py -3.14"));assert(launcher.indexOf('if exist ".venv\\Scripts\\python.exe"')<launcher.indexOf("py -3.15"));
assert(launcher.includes("<= (3,15)"));assert(launcher.includes("Py_GIL_DISABLED"));assert(!launcher.includes("%*"));
for(const name of ["START-WINDOWS.cmd","UPDATE-WINDOWS.cmd"]){assert(fs.readFileSync(path.join(root,name),"utf8").includes("sys.version_info.releaselevel == 'final'"));}
assert(html.includes("No HTML download or JSON move is required"));assert(html.includes("Reviewed Python compatibility and offline tests"));assert(html.includes("no cp315 wheel"));
const workflow=fs.readFileSync(path.join(root,".github/workflows/ci.yml"),"utf8");
assert(workflow.includes("os: [ubuntu-latest, windows-latest]"));assert(workflow.includes("python: ['3.11', '3.12', '3.13', '3.14', '3.15']"));
assert.equal((workflow.match(/allow-prereleases: false/g)||[]).length,3);
assert(workflow.includes("TECH_FARMER_REQUIRE_SDK: '1'"));assert(workflow.includes("TECH_FARMER_REQUIRE_VISION: '1'"));assert(workflow.includes("RandomWeightOwlv2ApiSmoke"));
assert(!/^\s*(?:continue-on-error|schedule|pull_request_target):/m.test(workflow));assert(!workflow.includes("contents: write"));assert(!workflow.includes("DISCORD_BOT_TOKEN"));assert(!workflow.includes("download-model"));
assert(workflow.includes("needs: [base-compatibility, optional-vision-api]"));assert(workflow.includes("always() && needs.base-compatibility.result == 'success'"));assert(workflow.includes("needs.optional-vision-api.result == 'skipped'"));
assert(workflow.includes("github.ref == 'refs/heads/main'"));assert(workflow.includes("github.event_name == 'push' || github.event_name == 'workflow_dispatch'"));
assert(workflow.includes("python -B tools/verify_manifest.py --archive"));assert(workflow.includes("path: .runtime/ci-source/TechFarmer-source.zip"));
console.log("PASS: offline setup DOM/OAuth, no secret/network inputs, menu/launcher and required workflow/static artifact gates. No native Windows or remote Actions execution claim.");
