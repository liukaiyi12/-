Page({
  data: {
    lines: []
  },
  onLoad() {
    const lines = [];
    try {
      const a = getApp();
      lines.push(a ? 'getApp() 正常' : 'getApp() 返回了 undefined');
      if (a && a.globalData) {
        lines.push('服务器地址：' + a.globalData.base);
      }
    } catch (e) {
      lines.push('getApp() 抛错：' + (e && e.message ? e.message : e));
    }
    try {
      lines.push('当前路由：' + this.route);
    } catch (e) {}
    lines.push('页面 JS 已执行到 onLoad');
    this.setData({ lines: lines });
  },

  goLogin() {
    wx.navigateTo({
      url: '/pages/login/index',
      fail: (e) => {
        wx.showModal({
          title: '跳转失败',
          content: JSON.stringify(e),
          showCancel: false
        });
      }
    });
  }
});
