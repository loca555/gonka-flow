// Read-only verification that a helper restart preserves existing test funds.
const {Interface}=require('../otc/node_modules/ethers');
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
const factoryAbi=require('../otc/artifacts/WgnkOtcFactory.json').abi;
const escrowAbi=require('../otc/artifacts/WgnkOtc.json').abi;
(async()=>{
 const origin='http://127.0.0.1:8790',base='http://127.0.0.1:8791';
 const config=await (await fetch(base+'/config',{headers:{Origin:origin}})).json();
 if(config.chainId!==31337||!config.simulation)throw Error('Not the local test helper');
 let id=0;
 async function rpc(method,params=[]){
  const response=await fetch(base+'/rpc',{method:'POST',headers:{Origin:origin,'Content-Type':'application/json','X-OTC-Session':config.session},body:JSON.stringify({jsonrpc:'2.0',id:++id,method,params})});
  const data=await response.json();if(!response.ok||data.error)throw Error('Read-only local RPC failed');return data.result;
 }
 const block=await rpc('eth_blockNumber');
 async function call(to,iface,name,args=[]){return iface.decodeFunctionResult(name,await rpc('eth_call',[{to,data:iface.encodeFunctionData(name,args)},block]))[0];}
 const ff=new Interface(factoryAbi),ef=new Interface(escrowAbi),tf=new Interface(['function balanceOf(address) view returns(uint256)']);
 const count=Number(await call(config.factory,ff,'count'));if(count>10000)throw Error('Unexpected test deal count');
 const state={factory:config.factory,accounts:config.accounts,balances:[],deals:[]};
 for(const account of config.accounts){const balances={account};
  for(const asset of Object.values(config.assets))balances[asset.symbol]=asset.symbol==='ETH'?BigInt(await rpc('eth_getBalance',[account,block])):await call(asset.address,tf,'balanceOf',[account]);
  state.balances.push(balances);
 }
 for(let i=0;i<count;i++){
  const address=await call(config.factory,ff,'dealAt',[i]),deal={address};
  for(const name of ['seller','buyer','quoteAsset','wgnkAmount','quoteAmount','sellerDeposit','buyerDeposit','executed'])deal[name]=await call(address,ef,name);
  state.deals.push(deal);
 }
 const body=JSON.stringify(state,(_,value)=>typeof value==='bigint'?value.toString():value);
 const hash=crypto.createHash('sha256').update(body).digest('hex');
 const target=path.resolve('test-results','otc-state-before.json');
 if(process.argv[2]==='before'){await fs.mkdir(path.dirname(target),{recursive:true});await fs.writeFile(target,JSON.stringify({hash,state:JSON.parse(body)},null,2));}
 else {const saved=JSON.parse(await fs.readFile(target,'utf8'));if(saved.hash!==hash)throw Error('Test balances/deposits changed during restart');}
 console.log(JSON.stringify({mode:process.argv[2],ok:true,factory:state.factory,deals:count,accounts:state.accounts.length,hash}));
})().catch(error=>{console.error(error.message);process.exitCode=1;});
