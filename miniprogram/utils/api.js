const app = getApp();

function request(path, method = 'GET', data) {
  return new Promise((resolve, reject) => {
    const header = { 'content-type': 'application/json' };
    if (app.globalData.token) header.Authorization = 'Bearer ' + app.globalData.token;
    wx.request({
      url: app.globalData.base + path,
      method: method,
      data: data || {},
      header: header,
      success(res) {
        if (res.statusCode === 401) {
          app.clearLogin();
          wx.reLaunch({ url: '/pages/login/index' });
          return reject({ message: '登录已过期' });
        }
        const body = res.data || {};
        if (body.ok) return resolve(body);
        reject(body.message ? body : { message: '请求失败（' + res.statusCode + '）' });
      },
      fail(err) {
        reject({ message: '连不上服务器：' + (err.errMsg || '请检查后端是否启动') });
      }
    });
  });
}

function toast(msg, icon) {
  wx.showToast({ title: String(msg).slice(0, 18), icon: icon || 'none', duration: 2000 });
}

module.exports = {
  get: (p) => request(p, 'GET'),
  post: (p, d) => request(p, 'POST', d),
  request,
  toast
};
