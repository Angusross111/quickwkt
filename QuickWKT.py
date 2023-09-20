# -*- coding: utf-8 -*-
"""
/***************************************************************************
Name                 : QuickWKT
Description          : QuickWKT
Date                 : 25/Oct/2016
copyright            : (C) 2011-2016 by ItOpen
email                : elpaso@itopen.it
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
from __future__ import absolute_import, print_function

from builtins import object, range, str

# Import the PyQt and QGIS libraries
from qgis.PyQt.QtCore import *
from qgis.PyQt.QtGui import *

try:
    from qgis.PyQt.QtWidgets import *
except:
    pass
import binascii
import inspect
import json
import os
import re
from numbers import Number

from qgis.core import *

# Import the code for the dialog
from .QuickWKTDialog import QuickWKTDialog

geojsontypes = ["Point", "LineString", "Polygon", "MultiPoint","MultiLineString","MultiPolygon","GeometryCollection","FeatureCollection"]
unsupportedTypes = ["MultiPoint"]
class QuickWKT(object):

    def __init__(self, iface):
        # Save reference to the QGIS interface
        self.iface = iface
        self.canvas = iface.mapCanvas()
        # TODO: remove: unused
        self.layerNum = 1
        iface.show_wkt = self.save_wkt
        iface.show_wkb = self.save_wkb
        iface.show_geometry = self.save_geometry

    def initGui(self):
        current_directory = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
        self.action = QAction(QIcon(os.path.join(current_directory, "icons", "quickwkt_icon.png")),
             "&QuickWKT", self.iface.mainWindow())
        # connect the action to the run method
        self.action.triggered.connect(self.quickwkt)

        # Add toolbar button and menu item

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("QuickWKT", self.action)

        # create dialog
        self.dlg = QuickWKTDialog()
        self.dlg.wkt.setPlainText("")
        self.dlg.layerTitle.setText('QuickWKT')

        self.dlg.clearButton.clicked.connect(self.clearButtonClicked)


    def clearButtonClicked(self):
        self.dlg.wkt.setPlainText('')

    def unload(self):
        # Remove the plugin menu item and icon
        self.iface.removePluginMenu("QuickWKT", self.action)
        self.iface.removeToolBarIcon(self.action)

     # run
    def quickwkt(self):
        # show the dialog
        self.dlg.show()
        self.dlg.adjustSize()
        result = self.dlg.exec_()
        # See if OK was pressed
        if result == 1 and self.dlg.wkt.toPlainText():
            text = str(self.dlg.wkt.toPlainText())
            text = text.strip("\'")
            layerTitle = self.dlg.layerTitle.text() or 'QuickWKT'
            try:
                if "(" in text:
                    self.save_wkt(text, layerTitle)
                if "{" in text:
                    self.save_geojson(text, layerTitle)
                else:
                    self.save_wkb(text, layerTitle)
            except Exception as e:
                # Cut
                message = self.constraintMessage(str(e))
                QMessageBox.information(self.iface.mainWindow(), \
                QCoreApplication.translate('QuickWKT', "QuickWKT plugin error"), \
                QCoreApplication.translate('QuickWKT', "There was an error with the service:<br /><strong>{0}</strong>").format(message))
                return

            # Refresh the map
            self.canvas.refresh()
            return

    def createLayer(self, typeString, layerTitle=None, crs=None):
        # Automatic layer title in case is None
        if not layerTitle:
            layerTitle = 'QuickWKT %s' % typeString
        if crs:
            crs = QgsCoordinateReferenceSystem(crs, QgsCoordinateReferenceSystem.PostgisCrsId)
        else:
            crs = self.canvas.mapSettings().destinationCrs()

        typeString = "%s?crs=%s" % (typeString, crs.authid())

        layer = QgsVectorLayer(typeString, layerTitle, "memory")

        # add attribute id, purely to make the features selectable from within attribute table
        layer.dataProvider().addAttributes([QgsField("name", QVariant.String)])
        try:
            registry = QgsMapLayerRegistry.instance()
        except:
            registry = QgsProject.instance()
        # First search for a layer with this name and type if the cbx is not checked
        if not self.dlg.cbxnewlayer.isChecked():
            for l in registry.mapLayersByName(layerTitle):
                if l.dataProvider().dataSourceUri() == layer.dataProvider().dataSourceUri():
                    return l
        registry.addMapLayer(layer)
        return layer

    def parseGeometryCollection(self, wkt, layerTitle=None):
        #Cannot use split as there are commas in the geometry.
        start = 20
        bracketLevel = -1
        for i in range(len(wkt)):
            if wkt[i] == '(':
                bracketLevel += 1
            elif wkt[i] == ')':
                bracketLevel -= 1
            elif wkt[i] == ',' and bracketLevel == 0:
                self.save_wkt(wkt[start:i], layerTitle)
                start = i + 1

        self.save_wkt(wkt[start:-1], layerTitle)

    def decodeBinary(self, wkb):
        """Decode the binary wkb and return as a hex string"""
        value = binascii.a2b_hex(wkb)
        value = value[::-1]
        value = binascii.b2a_hex(value)
        return value.decode("UTF-8")

    def encodeBinary(self, value):
        wkb = binascii.a2b_hex("%08x" % value)
        wkb = wkb[::-1]
        wkb = binascii.b2a_hex(wkb)
        return wkb.decode("UTF-8")

    def saveFeatures(self, layer, features):
        layer.dataProvider().addFeatures(features)
        layer.updateExtents()
        layer.reload()
        self.canvas.refresh()

    def save_wkb(self, wkb, layerTitle=None):
        """Shows the WKB geometry in the map canvas, optionally specify a
        layer name otherwise it will be automatically created
        Returns the layer where features has been added (or None)."""
        SRID_FLAG = 0x20000000

        typeMap = {0: "Point", 1: "LineString", 2: "Polygon"}
        srid = ""
        qDebug("Decoding binary: " + wkb)

        geomType = int("0x" + self.decodeBinary(wkb[2:10]), 0)
        if geomType & SRID_FLAG:
            srid = int("0x" + self.decodeBinary(wkb[10:18]), 0)
            # String the srid from the wkb string
            wkb = wkb[:2] + self.encodeBinary(geomType ^ SRID_FLAG) + wkb[18:]

        geom = QgsGeometry()
        geom.fromWkb(binascii.a2b_hex(wkb))
        wkt = geom.asWkt()
        qDebug("As wkt = " + wkt)
        qDebug("Geom type = " + str(geom.type()))
        if not wkt:
            qDebug("Geometry creation failed")
            return None
        f = QgsFeature()
        f.setGeometry(geom)
        layer = self.createLayer(typeMap[geom.type()], layerTitle, srid)

        self.saveFeatures(layer, [f])
        return layer

    def constraintMessage(self, message):
        """return a shortened version of the message"""
        if len(message) > 128:
            message = message[:64] + ' [ .... ] ' + message[-64:]
        return message

    def save_wkt(self, wkt, layerTitle=None):
        """Shows the WKT geometry in the map canvas, optionally specify a
        layer name otherwise it will be automatically created.
        Returns the layer where features has been added (or None)."""
        # supported types as needed for layer creation
        typeMap = {0: "Point", 1: "LineString", 2: "Polygon"}
        newFeatures = {}
        errors = ""
        regex = re.compile("([a-zA-Z]+)[\s]*(.*)")
        # Clean newlines where there is not a new object
        wkt = re.sub('\n *(?![SPLMC])', ' ', wkt)
        qDebug("wkt: " + wkt)
        # check all lines in text and try to make geometry of it, collecting errors and features
        for wktLine in wkt.split('\n'):
            wktLine = wktLine.strip()
            if wktLine:
                try:
                    wktLine = wktLine.upper().replace("LINEARRING", "LINESTRING")
                    results = re.match(regex, wktLine)
                    wktLine = results.group(1) + " " + results.group(2)
                    qDebug("Attempting to save '%s'" % wktLine)
                    #EWKT support
                    srid = ""
                    if wktLine.startswith("SRID"):
                        srid, wktLine = wktLine.split(";")  # SRID number
                        srid = int(re.match(".*?(\d+)", srid).group(1))
                        qDebug("SRID = '%d'" % srid)

                    #Geometry Collections
                    if wktLine.startswith("GEOMETRYCOLLECTION ("):
                        self.parseGeometryCollection(wktLine, layerTitle)
                        continue

                    geom = QgsGeometry.fromWkt(wktLine)
                    if not geom:
                        errors += ('-    "' + wktLine + '" is invalid\n')
                        continue

                    f = QgsFeature()
                    f.setGeometry(geom)
                    if geom.type() in newFeatures:
                        newFeatures.get(geom.type()).append((f, srid))
                    else:
                        newFeatures[geom.type()] = [(f, srid)]
                except:
                    errors += ('-    ' + wktLine + '\n')
        if len(errors) > 0:
            # TODO either quit or succeed ignoring the errors
            errors = self.constraintMessage(str(errors))
            infoString = QCoreApplication.translate('QuickWKT', "These line(s) are not WKT or not a supported WKT type:\n" + errors + "Do you want to ignore those lines (OK) \nor Cancel the operation (Cancel)?")
            res = QMessageBox.question(self.iface.mainWindow(), "Warning QuickWKT", infoString, QMessageBox.Ok | QMessageBox.Cancel)
            if res == QMessageBox.Cancel:
                return

        layer = None
        for typ in list(newFeatures.keys()):
            for f in newFeatures.get(typ):
                layer = self.createLayer(typeMap[typ], layerTitle , f[1])
                layer.dataProvider().addFeatures([f[0]])
                layer.updateExtents()
                layer.reload()
                try: # QGIS < 3
                    layer.setCacheImage(None)
                except:
                    pass
                self.canvas.refresh()
        return layer

    def save_geojson(self, geojson, layerTitle=None):
   
        jsonObj = geojson if isinstance(geojson,dict) else json.loads(geojson)
        if("geometry" in jsonObj and "type" in jsonObj["geometry"]):
            jsonObj = jsonObj["geometry"]
        if isinstance(jsonObj, list):
            for geom in jsonObj:
                self.save_geojson(geom,layerTitle)
        if "type" not in jsonObj:
            raise Exception("Invalid geojson, does not contain 'type'")

        if jsonObj["type"] not in geojsontypes:
            raise Exception("Invalid geojson, "+jsonObj["type"]+" is not a valid type")

        if jsonObj["type"] in unsupportedTypes:
            raise Exception("Unsupported type, "+jsonObj["type"]+" is not supported Yet")


        # feat = QgsFeature()
        feat = None
        if jsonObj["type"] == "Point":
            self.check_point(jsonObj["coordinates"])
            feat = QuickWKT.create_qgis_feature(jsonObj)
        elif jsonObj["type"] == "LineString":
            self.check_line_string(jsonObj["coordinates"])
            feat = QuickWKT.create_qgis_feature(jsonObj)
        elif jsonObj["type"] == "MultiLineString":
            self.check_multi_line_string(jsonObj["coordinates"])
            feat = QuickWKT.create_qgis_feature(jsonObj)
        elif jsonObj["type"] == "Polygon":
            self.check_polygon(jsonObj["coordinates"])
            feat = QuickWKT.create_qgis_feature(jsonObj)
        elif jsonObj["type"] == "MultiPolygon":
            self.check_multi_polygon(jsonObj["coordinates"])
            feat = QuickWKT.create_qgis_feature(jsonObj)
        elif jsonObj["type"] == "GeometryCollection":
            self.check_geometry_collection(jsonObj)
            geometryTypeDict = {}
            for geometry in jsonObj["geometries"]:
                if(geometry["type"] in ["Point", "LineString", "Polygon", "MultiPoint","MultiLineString","MultiPolygon"]):
                    if(geometry["type"] in geometryTypeDict):
                        geometryTypeDict[geometry["type"]].append(geometry)
                    else:
                        geometryTypeDict.update({geometry["type"]: [geometry]})

            for [type, arr] in geometryTypeDict.items(): 
                layer = self.createLayer(type, layerTitle)
                features = [QuickWKT.create_qgis_feature(geometry) for geometry in arr]
                layer.dataProvider().addFeatures(features)
                layer.updateExtents()
                layer.reload()
            return
        elif jsonObj["type"] == "FeatureCollection":
            self.check_feature_collection(jsonObj)
            geometryTypeDict = {}
            for feature in jsonObj["features"]:
                geometry = feature["geometry"]
                if(geometry["type"] in ["Point", "LineString", "Polygon", "MultiPoint","MultiLineString","MultiPolygon"]):
                    if(geometry["type"] in geometryTypeDict):
                        geometryTypeDict[geometry["type"]].append(geometry)
                    else:
                        geometryTypeDict.update({geometry["type"]: [geometry]})

            for [type, arr] in geometryTypeDict.items(): 
                layer = self.createLayer(type, layerTitle)
                features = [QuickWKT.create_qgis_feature(geometry) for geometry in arr]
                layer.dataProvider().addFeatures(features)
                layer.updateExtents()
                layer.reload()
            return

        layer = self.createLayer(jsonObj["type"], layerTitle)
        layer.dataProvider().addFeatures([feat])
        layer.updateExtents()
        layer.reload()
            


    def save_geometry(self, geometry, layerTitle=None):
        """Shows the QgsGeometry in the map canvas, optionally specify a
        layer name otherwise it will be automatically created.
        Returns the layer where features have been added (or None)."""
        if isinstance(geometry, QgsGeometry):
            return self.save_wkt(geometry.exportToWkt(), layerTitle)
        else:
            # fix_print_with_import
            print("Error: this is not an instance of QgsGeometry")
            return None


    @staticmethod
    def create_qgis_feature(geometry):
        feat = QgsFeature()
        if(geometry["type"] == "Polygon"):
            qgsPoints = [[QgsPointXY(coord[0],coord[1])  for coord in ring] for ring in geometry['coordinates']]
            gPolygon = QgsGeometry.fromPolygonXY(qgsPoints)
            feat.setGeometry(gPolygon)
        elif(geometry["type"] == "LineString"):
            qgsPoints = [QgsPoint(coord[0], coord[1]) for coord in geometry["coordinates"]]
            gLine = QgsGeometry.fromPolyline(qgsPoints)
            feat.setGeometry(gLine)
        elif(geometry["type"] == "MultiLineString"):
            multi = []
            for line in geometry['coordinates']:
                qgsPoints = [QgsPointXY(coord[0],coord[1])  for coord in line]
                
                multi.append(qgsPoints)
            feat.setGeometry(QgsGeometry.fromMultiPolylineXY(multi))
        elif(geometry["type"] == "Point"):
            gPnt = QgsGeometry.fromPointXY(QgsPointXY(geometry["coordinates"][0],geometry["coordinates"][1]))
            feat.setGeometry(gPnt)
        elif(geometry["type"] == "MultiPolygon"):
            multi = []
            for poly in geometry['coordinates']:
                qgsPoints = [QgsPointXY(coord[0],coord[1])  for coord in poly[0]]
                multi.append([qgsPoints])
            feat.setGeometry(QgsGeometry.fromMultiPolygonXY(multi))
        else:
            return None
        return feat
    @staticmethod   
    def check_point(coord):
        if not isinstance(coord, list):
           raise Exception('each position must be a list')
        if len(coord) not in (2, 3):
           raise Exception('a position must have exactly 2 or 3 values')
        for number in coord:
            if not isinstance(number, Number):
               raise Exception('a position cannot have inner positions')

    @staticmethod   
    def check_line_string(coord):
        if not isinstance(coord, list):
           raise Exception('each line must be a list of positions')
        if len(coord) < 2:
             raise Exception ('the "coordinates" member must be an array of '
                    'two or more positions')
        for pos in coord:
            error = QuickWKT.check_point(pos)
            if error:
                 raise Exception( error)

    @staticmethod
    def check_multi_line_string(coord):
        if not isinstance(coord, list):
           raise Exception('MultiLineStrings must be a list of lineStrings')
        for elem in coord:
            QuickWKT.check_line_string(elem) 

    @staticmethod   
    def check_polygon(coord):
        if not isinstance(coord, list):
           raise Exception('Each polygon must be a list of linear rings')

        if not all(isinstance(elem, list) for elem in coord):
             raise Exception( "Each element of a polygon's coordinates must be a list")

        lengths = all(len(elem) >= 4 for elem in coord)
        if lengths is False:
           raise Exception('Each linear ring must contain at least 4 positions')

        isring = all(elem[0] == elem[-1] for elem in coord)
        if isring is False:
           raise Exception('Each linear ring must end where it started')

    @staticmethod   
    def check_multi_polygon(coord):
        if not isinstance(coord, list):
           raise Exception('Each polygon must be a list of linear rings')

        for elem in coord:
            QuickWKT.check_polygon(elem) 

    @staticmethod
    def check_geometry_collection(geometryCollection):
        if not "geometries" in geometryCollection:
           raise Exception('Geometry collection must contain a geometries list')

        if not isinstance(geometryCollection["geometries"], list):
           raise Exception('Geometry collection geometries must be a list')

        for geometry in geometryCollection["geometries"]:
            if(geometry["type"] == "Polygon"):
                QuickWKT.check_polygon(geometry["coordinates"]) 
            elif(geometry["type"] == "LineString"):
                QuickWKT.check_line_string(geometry["coordinates"])
            elif(geometry["type"] == "Point"):
                QuickWKT.check_point(geometry["coordinates"]) 
            elif(geometry["type"] == "MultiPolygon"):
                QuickWKT.check_multi_polygon(geometry["coordinates"])
            else:
                raise Exception('Geometry collection geometries contains unsupported geometry'+geometry["type"])
    @staticmethod
    def check_feature_collection(featureCollection):
        if not "features" in featureCollection:
           raise Exception('Feature collection must contain a features list')

        if not isinstance(featureCollection["features"], list):
           raise Exception('Feature collection features must be a list')

        for feature in featureCollection["features"]:
            if not "type" in feature or feature["type"] != "Feature":
                raise Exception('Feature collection doesnt contain a valid feature')
            geometry = feature["geometry"]
            if(geometry["type"] == "Polygon"):
                QuickWKT.check_polygon(geometry["coordinates"]) 
            elif(geometry["type"] == "LineString"):
                QuickWKT.check_line_string(geometry["coordinates"])
            elif(geometry["type"] == "Point"):
                QuickWKT.check_point(geometry["coordinates"]) 
            elif(geometry["type"] == "MultiPolygon"):
                QuickWKT.check_multi_polygon(geometry["coordinates"])
            else:
                raise Exception('Geometry collection geometries contains unsupported geometry'+geometry["type"])

    def getLayer(self, layerId):
        for layer in list(QgsProject.instance().mapLayers().values()):
            if  layer.id().startsWith(layerId):
                return layer
        return None


if __name__ == "__main__":
    pass
