const api = require('../../utils/api');

Page({
  data: {
    name: '',
    address: '',
    lng: null,
    lat: null,
    locText: '还没取定位',
    saving: false
  },

  onName(e) { this.setData({ name: e.detail.value }); },
  onAddr(e) { this.setData({ address: e.detail.value }); },

  // 建档时顺手把店的位置定下来，以后打卡才有围栏可用
  loc() {
    wx.getLocation({
      type: 'gcj02',
      success: (r) => {
        this.setData({
          lng: r.longitude,
          lat: r.latitude,
          locText: '已取到：' + r.longitude.toFixed(5) + ', ' + r.latitude.toFixed(5)
        });
      },
      fail: () => api.toast('拿不到定位，可以先不填')
    });
  },

  save() {
    if (this.data.saving) return;
    if (!this.data.name.trim() || !this.data.address.trim()) {
      return api.toast('门店名和地址都要填');
    }
    this.setData({ saving: true });
    api.post('/api/stores', {
      name: this.data.name.trim(),
      address: this.data.address.trim(),
      lng: this.data.lng,
      lat: this.data.lat
    }).then((r) => {
      this.setData({ saving: false });
      wx.showModal({
        title: '建档成功',
        content: r.store.name + '（' + r.store.id + '）\n归到你名下，可以立刻去打卡。',
        showCancel: false,
        success: () => wx.navigateBack()
      });
    }).catch((e) => {
      this.setData({ saving: false });
      api.toast(e.message || '建档失败');
    });
  }
});
