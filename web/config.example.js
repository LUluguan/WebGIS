// config.example.js — 凭据配置模板(此文件入库, 真实凭据写在 config.local.js, 已 gitignore)
// 复制本文件为 web/config.local.js 并填入真实 key 即可。
window.FLOOD_KEYS = {
  tdt: "在此填入天地图 key(32 位)",  // https://console.tianditu.gov.cn 申请, 建议绑定域名
  ion: "在此填入 Cesium Ion token"   // https://ion.cesium.com/tokens
};
