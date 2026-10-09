/* 共享默认值：background 用 importScripts 引入，popup 用 <script src> 引入。
   两处必须同源——MV3 的 popup CSP 是 script-src 'self'，内联脚本会被静默拦掉。 */
const DEFAULT_BOARD_URL = "http://127.0.0.1:4000";
