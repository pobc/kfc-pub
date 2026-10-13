import { ApiError, publicApi } from './api.js';
import { cities } from './cities.js';
import { requiredCount, selectedOptions, initialSelection, selectionComplete, toggleSelection } from './option-selection.js';

const RECENT_KEY='recent-store-searches';
const HOT_CITY_NAMES=['杭州','北京','上海','深圳','成都','重庆','天津','南京','苏州','武汉','西安'];
const views=['loading','store','products','confirm','processing','coupon-applied','pickup','failed','completed','fatal'];
const state={accessId:'',step:'loading',city:null,store:null,products:[],quantities:{},optionSelections:{},packType:2,order:null,stores:[],recent:[],pollTimer:null,pollInFlight:false,online:navigator.onLine,verificationToken:'',verificationLoading:false};
const $=(id)=>document.getElementById(id);
const escapeHtml=(value)=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
const isVerificationError=(error)=>error instanceof ApiError&&error.status===428;

function setStep(step){state.step=step;views.forEach((name)=>{$(`${name}-view`).hidden=name!==step;});clearError();if(step==='pickup')renderPickup();else clearPoll();}
function showError(message){$('order-error').textContent=message||'';$('order-error').hidden=!message;}
function clearError(){showError('');}
function resultMessage(result,fallback){const msg=result?.msg||result?.message||fallback;const code=result?.code;if(code===undefined||code===null||String(code)==='')return msg;return `[${code}] ${msg}`;}
function selectedItems(){return state.products.filter((item)=>(state.quantities[item.id]||0)>0).map((item)=>({...item,quantity:state.quantities[item.id]}));}
function productOptionGroups(product){return Array.isArray(product?.optionGroups)?product.optionGroups:[];}
function selectionKey(productId,groupNo){return `${productId}:${groupNo}`;}
function selectedOptionIds(){return state.products.flatMap((product)=>productOptionGroups(product).flatMap((group)=>selectedOptions(group,state.optionSelections[selectionKey(product.id,group.groupNo)]).map((option)=>Number(option.id))));}
function allOptionsSelected(){return state.products.every((product)=>productOptionGroups(product).every((group)=>selectionComplete(group,state.optionSelections[selectionKey(product.id,group.groupNo)])));}
function initializeDefaultOptions(){state.products.forEach((product)=>productOptionGroups(product).forEach((group)=>{const key=selectionKey(product.id,group.groupNo);state.optionSelections[key]=initialSelection(group,state.optionSelections[key]);}));}
function setBusy(button,busy,text){if(!button.dataset.text)button.dataset.text=button.textContent;button.disabled=busy;button.textContent=busy?text:button.dataset.text;}
function clearPoll(){if(state.pollTimer)window.clearTimeout(state.pollTimer);state.pollTimer=null;}
function updateContinueButton(){const button=$('continue-order');const complete=allOptionsSelected();button.disabled=!complete;button.textContent=complete?'确认餐品':'请选满各组餐品';}

async function loadEntry(){setStep('loading');clearPoll();state.city=null;state.store=null;state.products=[];state.quantities={};state.optionSelections={};state.order=null;state.packType=2;try{const link=await publicApi.getLink(state.accessId);if(link.status==='FAILED'){setStep('failed');return;}if(link.status==='COMPLETED'){setStep('pickup');await pollOrderInfo();return;}if(link.status==='PROCESSING'||link.status==='PICKING_UP'||link.couponApplied){setStep('pickup');await pollOrderInfo();return;}state.products=Array.isArray(link.products)?link.products:[];initializeDefaultOptions();setStep('store');renderStoreSearch();}catch(error){if(isVerificationError(error))return;$('fatal-message').textContent=error instanceof Error?error.message:'链接校验失败';setStep('fatal');}}

function loadRecent(){try{const saved=JSON.parse(localStorage.getItem(RECENT_KEY)||'[]');state.recent=Array.isArray(saved)?saved.slice(0,3):[];}catch{state.recent=[];}renderRecent();}
function saveRecent(){if(!state.city||!state.stores.length)return;const current={city:state.city,query:$('store-query').value.trim()};state.recent=[current,...state.recent.filter((item)=>item.city.gbCityCode!==current.city.gbCityCode||item.query.toLowerCase()!==current.query.toLowerCase())].slice(0,3);localStorage.setItem(RECENT_KEY,JSON.stringify(state.recent));renderRecent();}
function renderRecent(){const box=$('recent-searches'),list=$('recent-search-list');box.hidden=!state.recent.length;list.innerHTML=state.recent.map((item,index)=>`<button type="button" data-recent="${index}"><strong>${escapeHtml(item.city.name)}</strong><span>${escapeHtml(item.query||'全部餐厅')}</span></button>`).join('');list.querySelectorAll('[data-recent]').forEach((button)=>button.addEventListener('click',async()=>{const item=state.recent[Number(button.dataset.recent)];state.city=item.city;$('store-query').value=item.query;renderStoreSearch();await searchStores();}));}

function renderStoreSearch(){$('city-label').textContent=state.city?.name||'请选择地区';$('store-query').disabled=!state.city;$('store-search-form').querySelector('button').disabled=!state.city;renderStores();}
function renderStores(){const list=$('store-list');list.innerHTML=state.stores.map((store,index)=>`<button type="button" class="store-card" data-store="${index}" ${store.status!=='OPEN'?'disabled':''}>${store.imageUrl?`<img class="store-card__image" src="${escapeHtml(store.imageUrl)}" alt="${escapeHtml(store.name)}" loading="lazy"/>`:''}<span class="status-dot ${store.status!=='OPEN'?'status-dot--closed':''}"></span><span class="store-card__body"><strong>${escapeHtml(store.name)}</strong><small>${escapeHtml(store.address)}</small><small>${escapeHtml(store.businessHours||'')} · ${store.status==='OPEN'?'可下单':'暂不可下单'}</small></span><b>›</b></button>`).join('');list.querySelectorAll('[data-store]').forEach((button)=>button.addEventListener('click',()=>chooseStore(state.stores[Number(button.dataset.store)])));$('store-empty').hidden=state.stores.length>0||!state.city;$('store-empty').textContent=state.city?`输入关键词搜索 ${state.city.name} 的餐厅`:'请选择地区后搜索餐厅';}
async function searchStores(){if(!state.city)return;const button=$('store-search-form').querySelector('button');setBusy(button,true,'搜索中');clearError();try{state.stores=(await publicApi.searchStores(state.accessId,state.city.gbCityCode,$('store-query').value)).stores||[];renderStores();saveRecent();}catch(error){if(isVerificationError(error))return;showError(error.message||'餐厅搜索失败');}finally{setBusy(button,false);}}
async function chooseStore(store){
  if(store.status!=='OPEN'||state.selectingStore)return;
  if(!store.imageUrl){showError('所选餐厅缺少图片，暂时无法匹配，请选择其他餐厅');return;}
  state.selectingStore=true;
  clearError();
  try{
    await publicApi.confirmStore(state.accessId,store,state.city?.name);
    state.store=store;
    state.quantities=Object.fromEntries(state.products.map((item)=>[item.id,item.minQty||1]));
    initializeDefaultOptions();
    renderProducts();
    setStep('products');
  }catch(error){
    if(!isVerificationError(error))showError(error.message||'餐厅选择保存失败，请重试');
  }finally{state.selectingStore=false;}
}

function renderOptionGroups(product){
  return productOptionGroups(product).map((group)=>{
    const key=selectionKey(product.id,group.groupNo);
    const selectedIds=state.optionSelections[key]||[];
    const count=requiredCount(group);
    const options=Array.isArray(group.options)?group.options:[];
    const optionButtons=options.map((option)=>{
      const selected=selectedIds.includes(Number(option.id));
      const hasImage=Boolean(option.imageUrl);
      const image=hasImage?`<img class="option-card__image" src="${escapeHtml(option.imageUrl)}" alt="${escapeHtml(option.name)}" loading="lazy"/>`:'';
      return `<button type="button" class="option-card ${hasImage?'':'option-card--no-image'} ${selected?'option-card--selected':''}" data-option-key="${escapeHtml(key)}" data-option-id="${Number(option.id)}" aria-pressed="${selected?'true':'false'}"><span class="option-card__check">✓</span>${image}<span class="option-card__body"><strong>${escapeHtml(option.name)}</strong><small>${selected?'已选':'选择'}</small></span></button>`;
    }).join('');
    const onlyOne=options.length===1;
    return `<section class="option-group ${count>1?'option-group--multiple':''}"><div class="option-group__title"><strong>${escapeHtml(group.name)}</strong><span class="option-group__progress" aria-live="polite">已选 ${selectedIds.length}/${count}</span></div><p class="option-group__hint">${count>1?`请选择 ${count} 个不同餐品，点击已选餐品可取消`:'请选择 1 个餐品，点击其他餐品可更换'}</p><div class="option-list ${onlyOne?'option-list--single':''}">${optionButtons}</div></section>`;
  }).join('');
}

function renderProducts(){
  const store=state.store;
  $('selected-store').innerHTML=`${store.imageUrl?`<img src="${escapeHtml(store.imageUrl)}" alt="${escapeHtml(store.name)}"/>`:''}<div><small>取餐餐厅</small><h2>${escapeHtml(store.name)}</h2><p>${escapeHtml(store.address)}</p><span>${escapeHtml(store.businessHours||'')}</span></div>`;
  $('product-list').innerHTML=state.products.map((product)=>`<article class="product-card">${product.imageUrl?`<img src="${escapeHtml(product.imageUrl)}" alt="餐品图片" loading="lazy"/>`:''}<div><p>${escapeHtml(product.description||'')}</p></div></article>${renderOptionGroups(product)}`).join('');
  $('product-list').querySelectorAll('[data-option-id]').forEach((button)=>button.addEventListener('click',()=>{
    const key=button.dataset.optionKey;
    const config=state.products.flatMap((product)=>productOptionGroups(product).map((group)=>({key:selectionKey(product.id,group.groupNo),group}))).find((entry)=>entry.key===key)?.group;
    if(!config)return;
    const current=state.optionSelections[key]||[];
    const optionId=Number(button.dataset.optionId);
    if(requiredCount(config)>1&&!current.includes(optionId)&&current.length>=requiredCount(config)){
      showError(`“${config.name}”已选满 ${requiredCount(config)} 个餐品，请先取消一个再更换`);
      return;
    }
    state.optionSelections[key]=toggleSelection(config,current,optionId);
    const group=button.closest('.option-group');
    if(group){
      group.querySelectorAll('[data-option-id]').forEach((card)=>{
        const selected=state.optionSelections[key].includes(Number(card.dataset.optionId));
        card.classList.toggle('option-card--selected',selected);
        card.setAttribute('aria-pressed',selected?'true':'false');
        const label=card.querySelector('.option-card__body small');
        if(label)label.textContent=selected?'已选':'选择';
      });
      group.querySelector('.option-group__progress').textContent=`已选 ${state.optionSelections[key].length}/${requiredCount(config)}`;
    }
    clearError();
    updateContinueButton();
  }));
  updateContinueButton();
}

function openConfirm(){if(!state.store||!selectedItems().length)return;if(!allOptionsSelected()){showError('请完成所有可选餐品选择');return;}renderConfirm();setStep('confirm');}
function renderConfirm(){
  $('confirm-store').innerHTML=`<small>取餐餐厅</small><strong>${escapeHtml(state.store.name)}</strong><p>${escapeHtml(state.store.address)}</p>`;
  $('confirm-items').innerHTML=selectedItems().map((item)=>{
    const optionRows=productOptionGroups(item).map((group)=>{
      const options=selectedOptions(group,state.optionSelections[selectionKey(item.id,group.groupNo)]);
      return `<div class="summary-option-row"><small>${escapeHtml(group.name)} · ${options.length} 项</small><div class="summary-option-names">${options.map((option)=>`<strong>${escapeHtml(option.name)}</strong>`).join('')}</div></div>`;
    }).join('');
    return `<div class="summary-row"><span>${escapeHtml(item.description||'餐品')}</span>${optionRows}</div>`;
  }).join('');
  renderPackType();
}
function renderPackType(){
  const dineButton=$('pack-dine');
  const takeawayButton=$('pack-takeaway');
  const dineSelected=state.packType===2;
  const takeawaySelected=state.packType===1;
  dineButton.classList.toggle('pickup-type-button--active',dineSelected);
  takeawayButton.classList.toggle('pickup-type-button--active',takeawaySelected);
  dineButton.setAttribute('aria-pressed',dineSelected?'true':'false');
  takeawayButton.setAttribute('aria-pressed',takeawaySelected?'true':'false');
  dineButton.querySelector('strong').innerHTML=dineSelected?'<span class="pack-check">✓</span> 堂食':'堂食';
  takeawayButton.querySelector('strong').innerHTML=takeawaySelected?'<span class="pack-check">✓</span> 外带':'外带';
}
async function applyCoupon(){if(!state.store||!state.store.storePayload){showError('餐厅原始数据缺失，请返回重新搜索并选择餐厅');return;}if(!allOptionsSelected()){showError('请返回完成可选餐品选择');return;}const button=$('apply-coupon');setBusy(button,true,'正在提交核销…');clearError();try{const result=await publicApi.applyCoupon(state.accessId,state.store.storeCode,state.store.storePayload,state.packType,selectedItems().map((item)=>({productId:item.id,quantity:item.quantity})),selectedOptionIds());if(result?.status==='PROCESSING'||result?.applied){setStep('pickup');await pollOrderInfo();return;}showError(resultMessage(result,'用券或确认订单失败'));}catch(error){if(isVerificationError(error))return;showError(error.message||'核销任务提交失败');}finally{setBusy(button,false);}}
function pickupNumberFor(order){return String(order?.queueNumber||'').trim()||String(order?.pickupCode||'').trim();}
function shouldPollOrder(){return !document.hidden&&state.step==='pickup'&&state.online&&!state.order?.stopPolling&&!pickupNumberFor(state.order);}
async function pollOrderInfo(){
  clearPoll();
  if(state.pollInFlight||!shouldPollOrder())return;
  state.pollInFlight=true;
  let verificationRequired=false;
  try{
    const order=await publicApi.getOrderInfo(state.accessId);
    if(state.step!=='pickup')return;
    state.order=order;
    clearError();
    if(order.stage==='FAILED'){setStep('failed');return;}
    if(order.stage==='COMPLETED'&&!pickupNumberFor(order)){setStep('completed');return;}
    renderPickup();
  }catch(error){
    verificationRequired=isVerificationError(error);
    if(!verificationRequired)showError(error.message||'订单信息暂时无法查询');
  }finally{
    state.pollInFlight=false;
    // 从同一个出口安排下一次查询，避免切回页面或恢复网络时重复轮询。
    if(!verificationRequired&&shouldPollOrder())state.pollTimer=window.setTimeout(()=>void pollOrderInfo(),2000);
  }
}
function formatItemDescription(value){const text=String(value||'').trim();if(!text)return'';try{const parsed=JSON.parse(text);if(Array.isArray(parsed))return parsed.filter(Boolean).join('、');}catch{return text;}return text;}
function renderPickupComponents(components){
  if(!Array.isArray(components)||!components.length)return'';
  return `<ul class="pickup-item-components">${components.map((component)=>`<li><span>${escapeHtml(component.name||'')}</span><b>×${Math.max(1,Number(component.quantity)||1)}</b>${renderPickupComponents(component.components)}</li>`).join('')}</ul>`;
}
function renderPickup(){
  const order=state.order||{};
  const pickupNumber=pickupNumberFor(order);
  const items=(Array.isArray(order.items)?order.items:[]).filter((item)=>String(item?.name||'').trim()&&!String(item.name).trim().startsWith('*'));
  $('pickup-eyebrow').textContent=pickupNumber?'取餐号已生成':(order.statusText||'订单处理中');
  $('pickup-title').textContent=pickupNumber?'请凭取餐号取餐':'正在获取取餐号';
  $('pickup-number').textContent=pickupNumber||'—';
  $('pickup-order-number').textContent=order.orderNumber||state.accessId||'—';
  $('pickup-loading').hidden=Boolean(pickupNumber)||Boolean(order.stopPolling);
  $('pickup-status').textContent=pickupNumber?'取餐号已生成，请留意餐厅叫号':`${order.statusText||'正在获取取餐号'}${order.stopPolling?'':'，页面每 2 秒自动更新'}`;
  $('pickup-store').innerHTML=`<small>取餐餐厅</small><strong>${escapeHtml(order.store?.name||'')}</strong><p>${escapeHtml(order.store?.address||'')}</p>`;
  $('pickup-item-list').innerHTML=items.length?items.map((item)=>{
    const description=formatItemDescription(item.description);
    const quantity=Math.max(1,Number(item.quantity)||1);
    return `<div class="pickup-item-entry"><div class="pickup-item-row"><span><strong>${escapeHtml(item.name)}</strong>${description?`<small>${escapeHtml(description)}</small>`:''}</span>${quantity>1?`<b>×${quantity}</b>`:''}</div>${renderPickupComponents(item.components)}</div>`;
  }).join(''):'<p class="pickup-items-empty">餐品信息暂未同步</p>';
}
function selectCity(city){if(!city)return;state.city=city;state.stores=[];$('store-query').value='';$('city-dialog').close();renderStoreSearch();}
function renderCities(query=''){const key=query.trim().toLowerCase();const list=cities.filter((city)=>!key||`${city.name}${city.province}${city.gbCityCode}`.toLowerCase().includes(key));const hotCities=key?[]:HOT_CITY_NAMES.map((name)=>cities.find((city)=>city.name===name)).filter(Boolean);const hotHtml=hotCities.length?`<section class="hot-city-section"><small>热门城市</small><div class="hot-city-grid">${hotCities.map((city)=>`<button type="button" data-city-code="${escapeHtml(city.gbCityCode)}">${escapeHtml(city.name)}</button>`).join('')}</div></section>`:'';const listHtml=list.map((city)=>`<button type="button" data-city-code="${escapeHtml(city.gbCityCode)}"><span><strong>${escapeHtml(city.name)}</strong><small>${escapeHtml(city.province)}</small></span><i>›</i></button>`).join('');$('city-list').innerHTML=hotHtml+listHtml;$('city-list').querySelectorAll('[data-city-code]').forEach((button)=>button.addEventListener('click',()=>selectCity(cities.find((city)=>city.gbCityCode===button.dataset.cityCode))));}

async function loadVerificationChallenge(){
  if(state.verificationLoading)return;
  state.verificationLoading=true;
  const errorBox=$('verification-error');
  errorBox.hidden=true;
  try{
    const challenge=await publicApi.getVerificationChallenge(state.accessId);
    if(!challenge?.required){if($('verification-dialog').open)$('verification-dialog').close();void loadEntry();return;}
    state.verificationToken=challenge.challengeToken;
    $('verification-image').src=challenge.image;
    $('verification-answer').value='';
    if(!$('verification-dialog').open)$('verification-dialog').showModal();
    $('verification-answer').focus();
  }catch(error){errorBox.textContent=error instanceof Error?error.message:'验证码加载失败';errorBox.hidden=false;if(!$('verification-dialog').open)$('verification-dialog').showModal();}
  finally{state.verificationLoading=false;}
}

async function submitVerification(event){
  event.preventDefault();
  const button=$('verification-submit');
  const errorBox=$('verification-error');
  setBusy(button,true,'验证中…');
  errorBox.hidden=true;
  try{
    await publicApi.verifyChallenge(state.accessId,state.verificationToken,$('verification-answer').value.trim());
    $('verification-dialog').close();
    state.verificationToken='';
    await loadEntry();
  }catch(error){await loadVerificationChallenge();errorBox.textContent=error instanceof Error?error.message:'验证失败';errorBox.hidden=false;}
  finally{setBusy(button,false);}
}

function bind(){state.accessId=decodeURIComponent(window.location.pathname.split('/').filter(Boolean).at(-1)||'');$('store-search-form').addEventListener('submit',(event)=>{event.preventDefault();void searchStores();});$('city-open').addEventListener('click',()=>{$('city-search').value='';renderCities();$('city-dialog').showModal();});$('city-close').addEventListener('click',()=>$('city-dialog').close());$('city-search').addEventListener('input',(event)=>renderCities(event.target.value));$('continue-order').addEventListener('click',openConfirm);$('apply-coupon').addEventListener('click',applyCoupon);$('pack-dine').addEventListener('click',()=>{state.packType=2;renderPackType();});$('pack-takeaway').addEventListener('click',()=>{state.packType=1;renderPackType();});document.querySelectorAll('[data-back]').forEach((button)=>button.addEventListener('click',()=>setStep(button.dataset.back.toLowerCase())));$('retry-link').addEventListener('click',loadEntry);document.addEventListener('visibilitychange',()=>{if(document.hidden)clearPoll();else if(state.step==='pickup')void pollOrderInfo();});window.addEventListener('online',()=>{state.online=true;$('offline-notice').hidden=true;if(state.step==='pickup')void pollOrderInfo();});window.addEventListener('offline',()=>{state.online=false;$('offline-notice').hidden=false;clearPoll();});window.addEventListener('public-verification-required',()=>void loadVerificationChallenge());$('verification-form').addEventListener('submit',submitVerification);$('verification-refresh').addEventListener('click',()=>void loadVerificationChallenge());$('verification-dialog').addEventListener('cancel',(event)=>event.preventDefault());}
bind();loadRecent();$('offline-notice').hidden=state.online;void loadEntry();
