// 本地标记数据存储管理
const STORAGE_KEY = 'interactive_map_data';

/**
 * 保存完整标记列表到 localStorage
 */
export const saveData = (data) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    return true;
  } catch (error) {
    console.error('保存数据失败:', error);
    return false;
  }
};

/**
 * 从 localStorage 读取标记列表
 */
export const loadData = () => {
  try {
    const storedData = localStorage.getItem(STORAGE_KEY);
    return storedData ? JSON.parse(storedData) : null;
  } catch (error) {
    console.error('加载数据失败:', error);
    return null;
  }
};

/**
 * 添加一个标记并持久化
 */
export const addMarker = (markerData) => {
  try {
    const currentData = loadData() || [];
    const newData = [...currentData, markerData];
    saveData(newData);
    return newData;
  } catch (error) {
    console.error('添加标记失败:', error);
    return null;
  }
};

/**
 * 删除指定 ID 的标记并持久化
 */
export const removeMarker = (markerId) => {
  try {
    const currentData = loadData();
    if (!currentData) return null;
    const newData = currentData.filter(marker => marker.id !== markerId);
    saveData(newData);
    return newData;
  } catch (error) {
    console.error('删除标记失败:', error);
    return null;
  }
};

/**
 * 更新指定 ID 的标记并持久化
 */
export const updateMarker = (markerId, newData) => {
  try {
    const currentData = loadData();
    if (!currentData) return null;
    const updatedData = currentData.map(marker =>
      marker.id === markerId ? { ...marker, ...newData } : marker
    );
    saveData(updatedData);
    return updatedData;
  } catch (error) {
    console.error('更新标记失败:', error);
    return null;
  }
};
