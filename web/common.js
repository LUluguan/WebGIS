// common.js — C2132 前端公共工具(最小安全集合, 2026-10 审计后收拢)
// 各页深色主题 CSS 与 Cesium Viewer 初始化暂保留页面内(视觉耦合重), 此处只收拢零争议逻辑。
(function (global) {
  "use strict";

  // 研究区(与后端 pipeline_config.py 同源)
  var CFG = {
    bounds: { west: 113.30, south: 23.09, east: 113.34, north: 23.13 },
    depthCap: 6,
    apiBase: ""   // 同源部署留空
  };

  // fetch JSON + r.ok 检查(旧版裸 fetch().then(json) 遇 4xx/5xx 会静默解析错误体)
  // + 15s AbortController 超时: 断网/服务挂起时快速失败, 不无限等待
  function getJSON(url, timeoutMs) {
    var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeoutMs || 15000) : null;
    return fetch(url, ctrl ? { signal: ctrl.signal } : undefined).then(function (r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error("HTTP " + r.status + " " + url);
      return r.json();
    }).catch(function (e) {
      clearTimeout(timer);
      if (e && e.name === "AbortError") throw new Error("请求超时: " + url);
      throw e;
    });
  }

  // 分级预警(与大屏/三维场景同一阈值: 城区淹没面积 0.6/1.0/1.3/1.6 km²)
  var WARN_STYLE = {
    "无":   { c: "#78909c", adv: "正常状态, 保持关注" },
    "蓝色": { c: "#42a5f5", adv: "关注积水, 低洼路段谨慎通行" },
    "黄色": { c: "#e6b800", adv: "避开地下车库与下穿隧道, 出行绕开易涝点" },
    "橙色": { c: "#ff8a65", adv: "减少外出, 低洼人员做好转移准备" },
    "红色": { c: "#ef5350", adv: "立即转移低洼人员, 开放应急避难场所" }
  };
  function warnLevelByArea(a) {
    return a >= 1.60 ? "红色" : a >= 1.30 ? "橙色" : a >= 1.00 ? "黄色" : a >= 0.60 ? "蓝色" : "无";
  }

  // 底图懒兜底: 页面只挂天地图(viewer 用 imageryProvider:false), 探测瓦片 4s 内拿不到
  // (403/断网)才动态把 Ion 世界影像插到最底层——避免两套全球底图从第一帧并行加载
  // (实测首屏省 60+ 外网请求; 国内 api.cesium.com 不稳时不再拖慢首屏)
  function basemapFallback(viewer, tdtKey) {
    var probe = "https://t0.tianditu.gov.cn/img_w/wmts?service=wmts&request=GetTile&version=1.0.0"
      + "&LAYER=img&tileMatrixSet=w&TileMatrix=8&TileRow=107&TileCol=212"
      + "&style=default&format=tiles&tk=" + (tdtKey || "");
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 4000);
    fetch(probe, { signal: ctrl.signal }).then(function (r) {
      clearTimeout(timer);
      if (!r.ok) throw new Error("HTTP " + r.status);
    }).catch(function (e) {
      clearTimeout(timer);
      try {
        var ion = new Cesium.IonImageryProvider({ assetId: 2 });   // Cesium World Imagery
        viewer.imageryLayers.add(ion, 0);                          // 插到最底层, 天地图仍在上面
        console.warn("天地图不可用(" + (e && e.message || e) + "), 已启用 Ion 影像兜底");
      } catch (e2) {
        console.warn("天地图不可用且 Ion 兜底失败:", e2);
      }
    });
  }

  global.FLOOD = {
    CFG: CFG,
    getJSON: getJSON,
    WARN_STYLE: WARN_STYLE,
    warnLevelByArea: warnLevelByArea,
    basemapFallback: basemapFallback
  };
})(window);
