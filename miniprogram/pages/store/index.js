const app = getApp();
const api = require('../../utils/api');

Page({
  data: {
    user: {},
    store: {},
    skus: [],
    cart: {},
    cartons: 0,
    amount: '0.00',
    loading: false
  },

  onLoad() {
    const u = app.globalData.user;
    if (!u) return wx.reLaunch({ url: '/pages/login/index' });
    this.setData({ user: u });
  },

  onShow() {
    this.load();
  },

  load() {
    Promise.all([api.get('/api/stores'), api.get('/api/master/skus')])
      .then(([st, sk]) => {
        this.setData({
          store: st.items[0] || {},
          skus: sk.items.filter((x) => x.can_order).map((x) => Object.assign({}, x, {
            qty: 0,
            priceText: x.price.toFixed(2)
          }))
        });
        this.recalc();
      }).catch((e) => api.toast(e.message));
  },

  step(e) {
    const i = e.currentTarget.dataset.i;
    const d = Number(e.currentTarget.dataset.d);
    const skus = this.data.skus;
    const q = (skus[i].qty || 0) + d;
    skus[i].qty = q < 0 ? 0 : q;
    this.setData({ skus: skus });
    this.recalc();
  },

  recalc() {
    let cartons = 0;
    let amt = 0;
    this.data.skus.forEach((s) => {
      if (s.qty > 0) {
        cartons += s.qty;
        amt += s.qty * s.price;
      }
    });
    this.setData({ cartons: cartons, amount: amt.toFixed(2) });
  },

  submit() {
    if (this.data.loading) return;
    const lines = this.data.skus
      .filter((s) => s.qty > 0)
      .map((s) => ({ sku_code: s.sku_code, cartons: s.qty }));
    if (!lines.length) return api.toast('还没选商品');
    this.setData({ loading: true });
    api.post('/api/orders', { store_id: this.data.store.id, lines: lines })
      .then((r) => {
        this.setData({ loading: false });
        wx.showModal({
          title: '下单成功',
          content: r.order_no + '\n金额 ¥' + r.amount.toFixed(2),
          showCancel: false
        });
        this.load();
      })
      .catch((err) => {
        this.setData({ loading: false });
        wx.showModal({
          title: '没下成',
          content: (err.message || '') +
            (err.missing && err.missing.length
              ? '\n缺：' + err.missing.map((m) => m.name).join('、') : ''),
          showCancel: false
        });
      });
  },

  goOrders() { wx.navigateTo({ url: '/pages/orders/index' }); },

  logout() {
    app.clearLogin();
    wx.reLaunch({ url: '/pages/login/index' });
  }
});
