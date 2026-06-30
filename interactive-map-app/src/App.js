import React, { useState } from 'react';
import MapContainer from './components/MapContainer';
import './App.css';

function App() {
  const [selectedRegion, setSelectedRegion] = useState(null);
  const [viewLevel, setViewLevel] = useState('world');
  const [chinaProvince, setChinaProvince] = useState(null);

  return (
    <div className="App">
      <MapContainer 
        viewLevel={viewLevel}
        setViewLevel={setViewLevel}
        chinaProvince={chinaProvince}
        setChinaProvince={setChinaProvince}
        selectedRegion={selectedRegion}
        setSelectedRegion={setSelectedRegion}
      />
    </div>
  );
}

export default App;