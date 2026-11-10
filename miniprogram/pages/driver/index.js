const app = getApp();
const api = require('../../utils/api');

Page({
  data: {
    user: {},
    items: [],
    pending: [],
    signed: [],
    next: null,
    amount: '0',
    pct: 0,
    loading: false
  },

  onLoad() {
    const u = app.globalData.user;
    if (!u) return wx.reLaunch({ url: '/pages/login/index' });
    this.setData({ user: u });
  },

  onShow() { this.load(); },

  load() {
    api.get('/api/deliveries').then((r) => {
      const items = r.items;
      const pending = items.filter((x) => x.status === '配送中');
      const signed = items.filter((x) => x.status === '已签收');
      const amount = pending.reduce((a, x) => a + x.amount, 0);
      this.setData({
        items: items,
        pending: pending,
        signed: signed,
        next: pending[0] || null,
        amount: amount.toFixed(0),
        pct: items.length ? Math.round(signed.length / items.length * 100) : 0
      });
    }).catch((e) => api.toast(e.message));
  },

  nav() {
    const n = this.data.next;
    if (!n) return;
    wx.showModal({
      title: '导航到 ' + n.store,
      content: n.address + '\n\n演示版不调起地图，生产版接 wx.openLocation。',
      showCancel: false
    });
  },

  sign() {
    const n = this.data.next;
    if (!n || this.data.loading) return;
    this.setData({ loading: true });
    wx.showModal({
      title: '确认签收',
      content: n.store + '\n代收货款 ¥' + n.amount.toFixed(2),
      success: (res) => {
        if (!res.confirm) return this.setData({ loading: false });
        api.post('/api/deliveries/' + n.delivery_no + '/sign', { receiver: n.store + ' 收货人' })
          .then((r) => {
            this.setData({ loading: false });
            wx.showToast({ title: '已签收', icon: 'success' });
            this.load();
          })
          .catch((err) => {
            this.setData({ loading: false });
            api.toast(err.message || '签收失败');
          });
      }
    });
  },

  report() {
    wx.showModal({
      title: '异常上报',
      content: '演示版未接表单。生产版要拍照 + 选原因（拒收 / 少货 / 关门）+ 通知内勤。',
      showCancel: false
    });
  },

  logout() {
    app.clearLogin();
    wx.reLaunch({ url: '/pages/login/index' });
  }
});
