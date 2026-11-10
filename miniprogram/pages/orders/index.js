const api = require('../../utils/api');

Page({
  data: {
    tab: 'orders',
    orders: [],
    ars: [],
    total: '0.00'
  },

  onShow() {
    if (!getApp().globalData.token) return wx.reLaunch({ url: '/pages/login/index' });
    this.load();
  },

  load() {
    api.get('/api/orders').then((r) => {
      const orders = r.items.map((o) => Object.assign({}, o, {
        amountText: o.amount.toFixed(2)
      }));
      this.setData({ orders: orders });
    }).catch((e) => api.toast(e.message));

    api.get('/api/receivables').then((r) => {
      let t = 0;
      const ars = r.items.map((x) => {
        t += x.amount;
        return Object.assign({}, x, { amountText: x.amount.toFixed(2) });
      });
      this.setData({ ars: ars, total: t.toFixed(2) });
    }).catch(() => {});
  },

  switchTab(e) {
    this.setData({ tab: e.currentTarget.dataset.t });
  },

  back() { wx.navigateBack(); }
});
