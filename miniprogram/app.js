// 全局：后端地址、登录态。改地址只改这一行。
App({
  globalData: {
    // 本地开发：微信开发者工具里勾选「不校验合法域名」即可访问
    // 上线必须换成 https + 已备案域名
    base: 'http://127.0.0.1:8765',
    token: '',
    user: null
  },

  onLaunch() {
    this.globalData.token = wx.getStorageSync('token') || '';
    this.globalData.user = wx.getStorageSync('user') || null;
  },

  saveLogin(token, user) {
    this.globalData.token = token;
    this.globalData.user = user;
    wx.setStorageSync('token', token);
    wx.setStorageSync('user', user);
  },

  clearLogin() {
    this.globalData.token = '';
    this.globalData.user = null;
    wx.removeStorageSync('token');
    wx.removeStorageSync('user');
  },

  // 角色 → 首页
  homeOf(role) {
    if (role === 'sales') return '/pages/sales/index';
    if (role === 'store') return '/pages/store/index';
    if (role === 'driver') return '/pages/driver/index';
    return '/pages/boss/index';
  }
});
