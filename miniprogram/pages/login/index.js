// 登录页刻意做成「零依赖」：不 require 任何模块。
// 之前白屏就是模块加载失败导致 Page() 没执行。
const BASE_DEFAULT = 'http://127.0.0.1:8765';

function baseUrl() {
  try {
    const a = getApp();
    if (a && a.globalData && a.globalData.base) return a.globalData.base;
  } catch (e) {}
  return BASE_DEFAULT;
}

function toast(msg) {
  wx.showToast({ title: String(msg).slice(0, 18), icon: 'none', duration: 2200 });
}

Page({
  data: {
    username: 'boss',
    password: 'demo1234',
    loading: false,
    errText: '',
    roles: [
      { u: 'sales1', n: '业务员 张伟', d: '打卡 / 拜访' },
      { u: 'store1', n: '门店 李老板', d: '订货 / 对账' },
      { u: 'driver1', n: '司机 李师傅', d: '配送 / 签收' },
      { u: 'boss', n: '老板 洪总', d: '看板 / 利润' }
    ]
  },

  onLoad() {
    // 已登录就跳首页；整段包 try，任何异常都不能让页面白屏
    try {
      const app = getApp();
      if (app && app.globalData && app.globalData.token && app.globalData.user) {
        wx.reLaunch({ url: app.homeOf(app.globalData.user.role) });
      }
    } catch (e) {
      console.warn('[login] 自动跳转跳过', e);
    }
  },

  onUser(e) { this.setData({ username: e.detail.value, errText: '' }); },
  onPwd(e) { this.setData({ password: e.detail.value, errText: '' }); },

  quick(e) {
    this.setData({ username: e.currentTarget.dataset.u }, () => this.login());
  },

  login() {
    if (this.data.loading) return;
    const u = (this.data.username || '').trim();
    const p = this.data.password || '';
    if (!u || !p) return toast('先填账号和密码');

    this.setData({ loading: true, errText: '' });
    wx.request({
      url: baseUrl() + '/api/auth/login',
      method: 'POST',
      header: { 'content-type': 'application/json' },
      data: { username: u, password: this.data.password },
      success: (res) => {
        this.setData({ loading: false });
        const d = (res && res.data) || {};
        if (!d.ok) {
          this.setData({ errText: d.message || ('登录失败（HTTP ' + res.statusCode + '）') });
          return;
        }
        try {
          const app = getApp();
          app.saveLogin(d.token, d.user);
          wx.reLaunch({ url: app.homeOf(d.user.role) });
        } catch (e) {
          this.setData({ errText: '登录成功但跳转失败：' + (e && e.message) });
        }
      },
      fail: (err) => {
        this.setData({ loading: false });
        const msg = (err && err.errMsg) || '';
        this.setData({
          errText: '连不上服务器。请确认后端已启动，且工具里勾了「不校验合法域名」。\n' + msg
        });
      }
    });
  }
});
