// 地图数据获取工具
import worldGeoJson from '../data/world.geo.json';
import chinaGeoJson from '../data/china.geo.json';

// 获取世界地图数据
export const getWorldData = async () => {
  // 在实际应用中，这里会从API或文件中加载真实数据
  // 现在返回模拟数据
  return Promise.resolve(worldGeoJson || {
    type: "FeatureCollection",
    features: []
  });
};

// 获取中国地图数据
export const getChinaData = async () => {
  // 返回包含所有中国省份的GeoJSON数据
  return Promise.resolve(chinaGeoJson || {
    type: "FeatureCollection", 
    features: []
  });
};

// 获取指定省份的地图数据
export const getProvinceData = async (provinceName) => {
  // 从中国省份数据中过滤出指定省份的数据
  const chinaData = await getChinaData();
  const provinceFeature = chinaData.features.find(feat => 
    feat.properties.name === provinceName
  );
  
  if (provinceFeature) {
    return Promise.resolve({
      type: "FeatureCollection",
      features: [provinceFeature]
    });
  } else {
    // 如果未找到指定省份，返回空数据
    return Promise.resolve({
      type: "FeatureCollection",
      features: []
    });
  }
};

// 获取所有省份数据
export const getAllProvincesData = async () => {
  return await getChinaData();
};