const app = getApp();
const api = require('../../utils/api');

Page({
  data: {
    username: 'boss',
    password: 'demo1234',
    loading: false,
    roles: [
      { u: 'sales1', n: '业务员 张伟', d: '打卡 / 拜访' },
      { u: 'store1', n: '门店 李老板', d: '订货 / 对账' },
      { u: 'driver1', n: '司机 李师傅', d: '配送 / 签收' },
      { u: 'boss', n: '老板 洪总', d: '看板 / 利润' }
    ]
  },

  onLoad() {
    if (app.globalData.token && app.globalData.user) {
      wx.reLaunch({ url: app.homeOf(app.globalData.user.role) });
    }
  },

  onUser(e) { this.setData({ username: e.detail.value }); },
  onPwd(e) { this.setData({ password: e.detail.value }); },

  quick(e) {
    this.setData({ username: e.currentTarget.dataset.u }, () => this.login());
  },

  login() {
    if (this.data.loading) return;
    this.setData({ loading: true });
    api.post('/api/auth/login', {
      username: this.data.username.trim(),
      password: this.data.password
    }).then((r) => {
      app.saveLogin(r.token, r.user);
      this.setData({ loading: false });
      wx.reLaunch({ url: app.homeOf(r.user.role) });
    }).catch((err) => {
      this.setData({ loading: false });
      api.toast(err.message || '登录失败');
    });
  }
});
