<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology" version="3.34.0">
  <pipe>
    <rasterrenderer alphaBand="-1" band="1" classificationMax="255" classificationMin="0" opacity="1" type="paletted">
      <colorPalette>
        <paletteEntry alpha="255" color="#e41a1c" label="1 building_land (建筑用地)" value="1"/>
        <paletteEntry alpha="255" color="#ff7f00" label="2 business_land (商业用地)" value="2"/>
        <paletteEntry alpha="255" color="#984ea3" label="3 industrial_land (工业用地)" value="3"/>
        <paletteEntry alpha="255" color="#ffd92f" label="4 transport_land (交通用地)" value="4"/>
        <paletteEntry alpha="255" color="#377eb8" label="5 infrastructure_land (基础设施/公共服务用地)" value="5"/>
        <paletteEntry alpha="255" color="#a6d854" label="6 agricultural_land (农业用地)" value="6"/>
        <paletteEntry alpha="255" color="#4dbbd5" label="7 fish_pond_land (鱼塘用地)" value="7"/>
        <paletteEntry alpha="255" color="#1f78b4" label="8 water_body (水体)" value="8"/>
        <paletteEntry alpha="255" color="#33a02c" label="9 mountainous_land (山地/自然地)" value="9"/>
        <paletteEntry alpha="255" color="#8c6d31" label="10 mangrove_land (红树林)" value="10"/>
        <paletteEntry alpha="255" color="#ffffff" label="255 unknown (未分类/空白)" value="255"/>
      </colorPalette>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
    <huesaturation colorizeBlue="128" colorizeGreen="128" colorizeOn="0" colorizeRed="255" colorizeStrength="100" grayscaleMode="0" invertColors="0" saturation="0"/>
    <rasterresampler maxOversampling="2">
      <bilinearRasterResampler/>
      <cubicRasterResampler/>
    </rasterresampler>
  </pipe>
  <customproperties>
    <property key="lumid_field" value="LUM_ID"/>
  </customproperties>
</qgis>

