const api = require('../../utils/api');

Page({
  data: {
    user: {},
    stats: { total: 0, done: 0, left: 0 },
    kpi: {},
    todo: [],
    done: [],
    markers: [],
    center: { lng: 121.5550, lat: 29.9080 },
    loading: false,
    lastMsg: ''
  },

  onLoad() {
    const u = getApp().globalData.user;
    if (!u) return wx.reLaunch({ url: '/pages/login/index' });
    this.setData({ user: u });
  },

  onShow() {
    this.loadToday();
    this.loadKpi();
  },

  loadToday() {
    api.get('/api/visits/today').then((r) => {
      const mk = [];
      r.todo.forEach((s) => {
        if (s.lng && s.lat) {
          mk.push({
            id: mk.length + 1, longitude: s.lng, latitude: s.lat, width: 22, height: 22,
            callout: { content: s.name, fontSize: 11, borderRadius: 6, padding: 4, display: 'BYCLICK' }
          });
        }
      });
      this.setData({ todo: r.todo, done: r.done, stats: r.stats, markers: mk });
    }).catch((e) => api.toast(e.message));
  },

  loadKpi() {
    api.get('/api/visits/kpi').then((r) => this.setData({ kpi: r })).catch(() => {});
  },

  // 打卡：先问要不要拍照留证，再拿定位，最后交给服务端算距离
  checkin(e) {
    if (this.data.loading) return;
    const id = e.currentTarget.dataset.id;
    wx.showActionSheet({
      itemList: ['直接打卡（不拍照）', '拍照打卡（留证，推荐）'],
      success: (r) => {
        if (r.tapIndex === 1) this.withPhoto(id);
        else this.doCheckin(id, []);
      },
      fail: () => {}
    });
  },

  withPhoto(id) {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sizeType: ['compressed'],
      sourceType: ['camera', 'album'],
      success: (res) => {
        const file = res.tempFiles[0];
        wx.showLoading({ title: '上传中' });
        wx.getFileSystemManager().readFile({
          filePath: file.tempFilePath,
          encoding: 'base64',
          success: (r) => {
            api.post('/api/upload', { name: 'checkin.jpg', b64: r.data })
              .then((up) => {
                wx.hideLoading();
                this.doCheckin(id, [up.url]);
              })
              .catch((err) => {
                wx.hideLoading();
                api.toast(err.message || '照片上传失败');
              });
          },
          fail: () => { wx.hideLoading(); api.toast('读不到照片'); }
        });
      },
      fail: () => {}
    });
  },

  doCheckin(id, photos) {
    const store = this.data.todo.find((s) => s.id === id);
    if (!store) return;
    this.setData({ loading: true });
    wx.getLocation({
      type: 'gcj02',
      success: (loc) => {
        api.post('/api/visits/checkin', {
          store_id: store.id,
          lng: loc.longitude,
          lat: loc.latitude,
          accuracy_m: loc.accuracy || 0,
          purpose: '回访',
          result: '已见到负责人',
          photos: photos
        }).then((r) => {
          this.setData({
            loading: false,
            lastMsg: (r.valid ? '有效拜访' : '无效：' + r.reason) +
              '（距门店 ' + (r.distance_m == null ? '?' : r.distance_m) + ' 米' +
              (photos.length ? '，已附照片' : '') + '）'
          });
          wx.showModal({
            title: r.valid ? '打卡成功' : '打卡已记录（无效）',
            content: (r.valid ? '围栏内，计有效拜访。' : r.reason) +
              '\n距离门店：' + (r.distance_m == null ? '—' : r.distance_m + ' 米') +
              (photos.length ? '\n已附现场照片 1 张' : '\n（未拍照，主管复核时会打问号）'),
            showCancel: false
          });
          this.loadToday();
          this.loadKpi();
        }).catch((err) => {
          this.setData({ loading: false });
          api.toast(err.message || '打卡失败');
        });
      },
      fail: () => {
        this.setData({ loading: false });
        wx.showModal({
          title: '拿不到定位',
          content: '请在小程序设置里允许「位置信息」，否则无法判断是否到店。',
          showCancel: false
        });
      }
    });
  },

  goNewStore() { wx.navigateTo({ url: '/pages/newstore/index' }); },

  goOrders() { wx.navigateTo({ url: '/pages/orders/index' }); },

  logout() {
    getApp().clearLogin();
    wx.reLaunch({ url: '/pages/login/index' });
  }
});
