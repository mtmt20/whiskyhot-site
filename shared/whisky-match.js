
// pref: {sweetness, smoky, fruity, spicy, body, menthol, abv, cask, rye} 형태의 취향 벡터.
// whiskies: build_whisky_taste_json()이 만든 배열. opts.priceMin/priceMax로 가격 필터,
// opts.excludeUrls로 이미 추천/보유한 위스키 제외 가능(전부 선택 사항).
// 반환값은 whiskies 원소를 가까운 순으로 정렬한 배열(스코어는 안 붙여서 반환 - 호출부가
// .slice(0, n)만 하면 되게 단순화)
function matchWhiskies(pref, whiskies, opts) {
  opts = opts || {};
  var FLAVOR_KEYS = ["sweetness", "smoky", "fruity", "spicy", "body", "menthol"];
  var pool = whiskies;
  if (opts.priceMin != null || opts.priceMax != null) {
    pool = pool.filter(function(w) {
      if (w.price == null) return false;
      if (opts.priceMin != null && w.price < opts.priceMin) return false;
      if (opts.priceMax != null && w.price > opts.priceMax) return false;
      return true;
    });
  }
  if (opts.excludeUrls && opts.excludeUrls.length) {
    pool = pool.filter(function(w) { return opts.excludeUrls.indexOf(w.url) === -1; });
  }
  return pool.map(function(w) {
    var dist = 0;
    FLAVOR_KEYS.forEach(function(k) {
      var pv = pref[k] != null ? pref[k] : 0;
      dist += Math.pow((w[k] || 0) - pv, 2);
    });
    dist = Math.sqrt(dist);
    // /10로 나누면 도수 취향이 향미 5축 거리에 묻혀서 결과가 잘 안 바뀌는 문제가 있었음
    // (실측 확인) - /3으로 줄여서 도수 취향이 실제로 순위에 반영되게 함
    var abvDist = (pref.abv != null && w.abv != null) ? Math.abs(w.abv - pref.abv) / 3 : 0;
    // 캐스크는 "맞으면 보너스"만 있으면 신호가 약해서 안 맞아도 크게 안 밀림 - 취향강도 x
    // 위스키 캐스크방향을 곱해서, 맞으면 보상 안 맞으면 감점이 같이 걸리게 함
    var caskTerm = (pref.cask != null) ? -(pref.cask * (w.cask || 0)) * 2.2 : 0;
    // 라이(호밀) 위스키는 캐스크가 아니라 원료 축이라 별도 보너스로 계산
    var ryeBonus = (pref.rye > 0.3 && w.rye) ? 0.8 : 0;
    return { w: w, score: dist + abvDist + caskTerm - ryeBonus };
  }).sort(function(x, y) { return x.score - y.score; }).map(function(s) { return s.w; });
}
