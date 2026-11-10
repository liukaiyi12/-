const app = getApp();
const api = require('../../utils/api');

Page({
  data: {
    user: {},
    d: {},
    orders: [],
    flow: [],
    hasCost: false
  },

  onLoad() {
    const u = app.globalData.user;
    if (!u) return wx.reLaunch({ url: '/pages/login/index' });
    this.setData({ user: u, hasCost: !!u.see_cost });
  },

  onShow() { this.load(); },

  load() {
    api.get('/api/dashboard').then((r) => {
      const order = ['已确认', '待发货', '配送中', '已回单'];
      const flow = order.map((s) => ({ s: s, n: r.by_status[s] || 0 }));
      this.setData({
        d: {
          orders: r.orders,
          amount: r.amount.toFixed(2),
          ar: r.ar.toFixed(2),
          limit: r.credit_limit.toFixed(0),
          margin: r.gross_margin
        },
        flow: flow
      });
    }).catch((e) => api.toast(e.message));

    api.get('/api/orders').then((r) => {
      this.setData({
        orders: r.items.slice(0, 6).map((o) => Object.assign({}, o, {
          amountText: o.amount.toFixed(2)
        }))
      });
    }).catch(() => {});
  },

  logout() {
    app.clearLogin();
    wx.reLaunch({ url: '/pages/login/index' });
  }
});
