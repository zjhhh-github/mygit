import React, { useEffect, useRef, useState } from 'react';
import { MapContainer as LeafletMap, TileLayer, GeoJSON, Marker, Popup } from 'react-leaflet';
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import ControlPanel from './ControlPanel';
import InfoPanel from './InfoPanel';
import { getWorldData, getChinaData, getProvinceData } from '../data/mapData';
import { loadData, saveData, addMarker as saveNewMarker, removeMarker as removeSavedMarker } from '../utils/storage';

// 修复Leaflet图标问题
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

const MapContainer = ({ 
  viewLevel, 
  setViewLevel, 
  chinaProvince, 
  setChinaProvince, 
  selectedRegion, 
  setSelectedRegion 
}) => {
  const mapRef = useRef();
  const [geoJsonData, setGeoJsonData] = useState(null);
  const [markers, setMarkers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [addingMarker, setAddingMarker] = useState(false);

  // 加载地图数据
  useEffect(() => {
    const loadMap = async () => {
      setLoading(true);
      
      try {
        let data;
        
        if (viewLevel === 'world') {
          data = await getWorldData();
        } else if (viewLevel === 'china-province') {
          data = await getChinaData();
        } else if (viewLevel === 'china-city' && chinaProvince) {
          data = await getProvinceData(chinaProvince);
        }
        
        // 从 localStorage 加载已保存的标记数据（同步操作）
        const savedMarkers = loadData();
        setMarkers(savedMarkers || []);
        
        setGeoJsonData(data);
      } catch (error) {
        console.error('加载地图数据失败:', error);
      } finally {
        setLoading(false);
      }
    };

    loadMap();
  }, [viewLevel, chinaProvince]);

  // 处理地图点击事件（用于添加标记）
  useEffect(() => {
    if (!mapRef.current || !addingMarker) return;

    const handleClick = (e) => {
      const { lat, lng } = e.latlng;
      
      // 创建新标记
      const newMarker = {
        id: Date.now(),
        lat,
        lng,
        region: selectedRegion?.name || 'Unknown',
        personName: '新人员',
        address: `${lat.toFixed(6)}, ${lng.toFixed(6)}`
      };
      
      // 保存到状态和本地存储
      const updatedMarkers = [...markers, newMarker];
      setMarkers(updatedMarkers);
      saveNewMarker(newMarker);
      
      // 取消添加模式
      setAddingMarker(false);
    };

    if (mapRef.current && addingMarker) {
      mapRef.current.on('click', handleClick);
    }

    return () => {
      if (mapRef.current) {
        mapRef.current.off('click', handleClick);
      }
    };
  }, [addingMarker, markers, selectedRegion]);

  // 处理区域点击事件
  const handleRegionClick = (regionName, regionData) => {
    if (viewLevel === 'world' && regionName === 'China') {
      // 点击中国，进入省级视图
      setViewLevel('china-province');
    } else if (viewLevel === 'china-province' && regionData?.admin) {
      // 点击省份，进入市级视图
      setChinaProvince(regionData.admin);
      setViewLevel('china-city');
    } else {
      // 显示区域详情
      setSelectedRegion({
        name: regionName,
        ...regionData
      });
    }
  };

  // 地理数据样式
  const geoJsonStyle = {
    fillColor: '#3182bd',
    weight: 1,
    opacity: 1,
    color: 'white',
    dashArray: '3',
    fillOpacity: 0.7
  };

  // 高亮样式
  const highlightStyle = {
    ...geoJsonStyle,
    weight: 3,
    color: '#666',
    dashArray: '',
    fillOpacity: 0.9
  };

  // 区域样式函数
  const styleFunction = (feature) => {
    return geoJsonStyle;
  };

  // 鼠标悬停效果
  const highlightFeature = (e) => {
    const layer = e.target;
    layer.setStyle(highlightStyle);
    layer.bringToFront();
  };

  // 取消高亮
  const resetHighlight = (e) => {
    const layer = e.target;
    layer.setStyle(geoJsonStyle);
  };

  // 添加交互功能到每个区域
  const onEachFeature = (feature, layer) => {
    const regionName = feature.properties.NAME || feature.properties.name || 'Unknown';
    
    layer.on({
      mouseover: highlightFeature,
      mouseout: resetHighlight,
      click: () => handleRegionClick(regionName, feature.properties)
    });
  };

  // 处理标记点击事件
  const handleMarkerClick = (marker) => {
    // 显示标记信息
    setSelectedRegion({
      name: marker.personName || `标记 ${marker.id}`,
      ...marker
    });
  };

  // 删除标记
  const handleDeleteMarker = (markerId, e) => {
    e.stopPropagation(); // 防止事件冒泡
    
    // 从状态和本地存储中删除标记
    const updatedMarkers = markers.filter(marker => marker.id !== markerId);
    setMarkers(updatedMarkers);
    removeSavedMarker(markerId);
  };

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <ControlPanel 
        viewLevel={viewLevel} 
        setViewLevel={setViewLevel}
        setChinaProvince={setChinaProvince}
        selectedRegion={selectedRegion}
        onAddMarker={() => setAddingMarker(!addingMarker)}
        addingMarker={addingMarker}
      />
      
      {selectedRegion && (
        <InfoPanel 
          region={selectedRegion} 
          onClose={() => setSelectedRegion(null)}
          markers={markers.filter(m => m.region === selectedRegion.name)}
        />
      )}
      
      {loading && (
        <div className="loading">加载地图数据中...</div>
      )}

      <LeafletMap
        ref={mapRef}
        center={[30, 0]}
        zoom={2}
        style={{ height: '100%', width: '100%' }}
        zoomControl={true}
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        />
        
        {geoJsonData && (
          <GeoJSON
            data={geoJsonData}
            style={styleFunction}
            onEachFeature={onEachFeature}
          />
        )}
        
        {/* 渲染所有标记 */}
        {markers.map((marker) => (
          <Marker
            key={marker.id}
            position={[marker.lat, marker.lng]}
            eventHandlers={{
              click: () => handleMarkerClick(marker),
            }}
          >
            <Popup>
              <div>
                <p><strong>{marker.personName}</strong></p>
                <p>{marker.address}</p>
                <button 
                  onClick={(e) => handleDeleteMarker(marker.id, e)}
                  style={{
                    backgroundColor: '#f44336',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '4px 8px',
                    cursor: 'pointer',
                    fontSize: '12px'
                  }}
                >
                  删除
                </button>
              </div>
            </Popup>
          </Marker>
        ))}
      </LeafletMap>
    </div>
  );
};

export default MapContainer;