#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Mar  9 10:34:57 2019

This module is part of OBIA4RTM.

Copyright (c) 2019 Lukas Graf

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

@author: Lukas Graf, graflukas@web.de
"""
import psycopg2
import os
import sys
import prosail
import numpy as np
import json
import OBIA4RTM
import OBIA4RTM.configurations.connect_db as connect_db
import OBIA4RTM.inversion.lookup_table as lut
from OBIA4RTM.inversion.handle_metadata import get_resampler
from OBIA4RTM.inversion.handle_prosail_cfg import read_params_per_class
from OBIA4RTM.mdata_proc.parse_s2xml import parse_s2xml
from OBIA4RTM.mdata_proc.get_scene_metadata import get_mean_angles
from OBIA4RTM.mdata_proc.get_scene_metadata import get_sun_zenith_angle
from OBIA4RTM.mdata_proc.get_scene_metadata import get_sensor_and_sceneid
from OBIA4RTM.mdata_proc.get_scene_metadata import get_scene_footprint
from OBIA4RTM.mdata_proc.get_scene_metadata import get_acqusition_time


class inversion:
    """
    super-class for the object-based inversion of satellite scenes
    """
    def __init__(self, xml_file, shp, raster):
        """
        class constructor for the inversion class
        """
        self.shp = shp
        self.raster = raster
        self.xml_file = xml_file
        self.__metadata = None
        self.__sensor = None
        self.__scene_id = None
        self.acquisition_time, self.acquisition_date = None, None
        # angles
        self.__tts, self.__tto, self.__psi = None, None, None
        # setup the DB connection
        self.conn, self.cursor = connect_db.connect_db()
        print('Connected to PostgreSQL engine sucessfully!')
    # end __init__


    @staticmethod
    def set_ProSAIL_config(path_to_config=None):
        """
        read in the config file holding the vegetation parameters for
        setting up the lookup table using the ProSAIL radiative transfer
        model

        Parameters
        ----------
        path_to_config : String
            optinal, path and filename of config-file for ProSAIL

        Returns
        -------
        path_to_config : String
            definite location of the config file or error if file not found
        """
        # in case path_to_config is None use the default location in the root
        # of OBIA4RTM (prosail.cfg)
        if path_to_config is None:
            directory = os.path.dirname(OBIA4RTM.__file__)
            path_to_config = directory + os.sep + 'prosail.txt'
        if (not os.path.isfile(path_to_config)):
            print("ERROR: Unable to locate the config file for PROSAIL!")
            sys.exit(-1)
        # endif
        return path_to_config
    # end set_ProSAIL_config


    @staticmethod
    def set_landcover_config(path_to_lc_config=None):
        """
        read in the land cover config file holding the land cover classes for
        setting up the lookup table using the ProSAIL radiative transfer
        model

        Parameters
        ----------
        path_to_lc_config : String
            optinal, path and filename of config-file for land cover classes

        Returns
        -------
        path_to_config : String
            definite location of the config file or error if file not found
        """
        # in case path_to_config is None use the default location in the root
        # of OBIA4RTM (prosail.cfg)
        if path_to_lc_config is None:
            directory = os.path.dirname(OBIA4RTM.__file__)
            path_to_lc_config = directory + os.sep + 'landcover.cfg'
        if (not os.path.isfile(path_to_lc_config)):
            print("ERROR: Unable to locate the config file for land cover classes!")
            sys.exit(-1)
        # endif
        return path_to_lc_config
    # end set_ProSAIL_config
    
    @staticmethod
    def set_soilrefl(path_to_soilrefl_file=None):
        """
        set up the file-path to the txt file containing
        the soil-reflectance required for ProSAIL to account for
        the soil background and read in the values

        Parameters
        ----------
        path_to_soilrefl_file : String
            optional, file to the txt file with soil reflectance values

        Returns
        -------
        soils : Numpy Array
            array of soil reflectance values (1 nm steps)
        """
        if path_to_soilrefl_file is None:
            directory = os.path.dirname(OBIA4RTM.__file__)
            path_to_soilrefl_file = directory + os.sep + 'soil_reflectance.txt'
        if (not os.path.isfile(path_to_soilrefl_file)):
            print("ERROR: Unable to locate the soil_reflectance.txt file!")
            sys.exit(-1)
        soils = np.genfromtxt(path_to_soilrefl_file)
        return soils
    # end set_soilrefl_file


    def get_scene_metadata(self):
        """
        reads the Sen2Core metadata file in case Sentinel-2 is used and sets
        the inversion class variables accordingly
        """
        self.__metadata = parse_s2xml(self.xml_file)
        try:
            assert self.__metadata is not None
        except AssertionError:
            print('Scene metadata could not be read!')
            sys.exit(-1)


class lut_inversion(inversion):
    """
    extends the inversion super-class for the lookup-table based
    approach
    """
    def insert_scene_metadata(self, metadata_table):
        """
        inserts the most important scene metadata before starting the inversion
        procedure into the OBIA4RTM PostgreSQL database

        Parameters
        ----------
        metadata_table : String
            name of table in which scene metadata should be written to
        
        Returns
        -------
        None
        """
        # get sensor and scene_id
        self.__sensor, self.__scene_id = get_sensor_and_sceneid(self.__metadata)
        # get mean angles from scene-metadata
        # tto -> sensor zenith angle
        # psi -> relative azimuth angle between sensor and sun
        self.__tto, self.__psi = get_mean_angles(
               self.__metadata)
        # sun zenith angle
        self.__tts = get_sun_zenith_angle(self.__metadata)
        # get the footprint already as PostGIS insert statment
        footprint_statement = get_scene_footprint(self.__metadata)
        # full metadata as JSON
        metadata_json = json.dumps(self.__metadata)
        # storage drive and filename of the image raster data
        splitted = os.path.split(self.raster)
        storage_drive = splitted[0]
        filename = splitted[1]
        # get acquisition time and date
        self.acquisition_time, self.acquisition_date = get_acqusition_time(
                self.__metadata)
        # insert this basic metadata direclty into the OBIA4RTM database before
        # continuing
        statement = "INSERT INTO {0} (acquisition_time, scene_id, sun_zenith, "\
                    "obs_zenith, rel_azimuth, sensor, footprint, full_description, "\
                    "storage_drive, filename) VALUES ('{1}','{2}',{3},{4},{5},"\
                    "'{6}',{7},'{8}','{9}','{10}');".format(
                            metadata_table,
                            self.acquisition_time,
                            self.__scene_id,
                            self.__tts,
                            self.__tto,
                            self.__psi,
                            self.__sensor,
                            footprint_statement,
                            metadata_json,
                            storage_drive,
                            filename
                            )
        try:
            self.cursor.execute(statement)
            self.conn.commit()
        except psycopg2.DatabaseError as err:
            print(err)


    def gen_lut(self, inv_table):
        """
        Generates the lookup table and stores it in the DB
        """
        # basic setup first
        # default soil-spectra -> use soil_reflectance fro
        soils = self.set_soilrefl()
        # get S2 sensor-response function
        resampler = get_resampler(self.conn, self.cursor, self.__sensor)

        # params that could be inverted
        list_of_params = ['n', 'cab', 'car', 'cbrown', 'cw', 'cm', 'lai',
                          'lidfa', 'lidfb', 'rsoil', 'psoil', 'hspot',
                          'tts', 'tto', 'psi', 'typelidf']
        # firstly, create the LUT from the params config file for the
        # defined land cover classes
        prosail_config = self.set_ProSAIL_config()
        landcover_config = self.set_landcover_config()

        # read in the landcover class information and the corresponding
        # prosail parameter setup
        params_container = read_params_per_class(prosail_config,
                                                 landcover_config)
        # extract the land cover classes
        lc_keys = list(params_container.keys())
        # loop over the land cover classes and generate the LUT per class
        for lc in lc_keys:
            # extract the land cover code and semantics
            lc_code = lc[0]  # code
            lc_sema = lc[1]  # meaning
            # get the ProSAIL parameters
            params = params_container.get(lc)
            param_lut = lut()
            param_lut.generate_param_lut(params)
            print("INFO: Start to generate ProSAIL-LUT for class '{0}' with "\
                  "{1} simulations".format(
                    lc_sema,
                    param_lut.lut_size))
            params_inv = dict()
            for ii in range(param_lut.to_be_inv[0].shape[0]):
                params_inv[str(ii)] = list_of_params[param_lut.to_be_inv[0][ii]]
            # convert to json
            params_inv_json = json.dumps(params_inv)
            # write the metadata into the inversion_mapping table
            insert = "INSERT INTO inversion_mapping (acquisition_date, " \
                     "params_to_be_inverted, landuse, sensor, scene_id) " \
                     "VALUES('{0}', '{1}', {2}, '{3}', '{4}');".format(
                             self.acquisition_dae,
                             params_inv_json,
                             lc_code,
                             self.__sensor,
                             self.__scene_id)
            try:
                self.cursor.execute(insert)
                self.conn.commit()
            except psycopg2.DatabaseError as err:
                print("Failed to insert metadata of inversion process!")
                print(err)   

            # loop over the parameters stored in the LUT and generate the 
            # according synthetic spectra
            for ii in range(param_lut.lut_size):
                
                # run ProSAIL for each combination in the LUT
                n = param_lut.lut[0,ii]
                cab = param_lut.lut[1,ii]
                car = param_lut.lut[2,ii]
                cbrown = param_lut.lut[3,ii]
                cw = param_lut.lut[4,ii]
                cm = param_lut.lut[5,ii]
                lai = param_lut.lut[6,ii]
                lidfa = param_lut.lut[7,ii]
                lidfb = param_lut.lut[8,ii]
                rsoil = param_lut.lut[9,ii]
                psoil = param_lut.lut[10,ii]
                hspot = param_lut.lut[11,ii]
                typelidf = param_lut.lut[15,ii]
                
                # run prosail in forward mode -> resulting spectrum is from 
                # 400 to 2500 nm in 1nm steps
                # use Python ProSAIL bindings
                spectrum = prosail.run_prosail(n,
                                               cab,
                                               car,
                                               cbrown,
                                               cw,
                                               cm,
                                               lai,
                                               lidfa,
                                               hspot,
                                               self.__tts,
                                               self.__tto,
                                               self.__psi,
                                               ant=0.0,
                                               alpha=40.,
                                               prospect_version="5", 
                                               typelidf=typelidf,
                                               lidfb=lidfb,
                                               rsoil0=soils[:,0],
                                               rsoil=1.,
                                               psoil=1.,
                                               factor="SDR")
                # resample to SRF of sensor
                # perform resampling from 1nm to S2-bands
                sensor_spectrum = resampler(spectrum)

                # convert to % reflectance
                sensor_spectrum *= 100.

                # store the results in DB
                insert_statement = "INSERT INTO {0} (id, n, cab, car, cbrown, cw, cm, " \
                                    "lai, lidfa, lidfb, rsoil, psoil, hspot, tts, tto, psi, typelidf, " \
                                    "b2, b3, b4, b5, b6, b7, b8a, b11, b12, acquisition_date, landuse) VALUES " \
                                    "({1}, {2}, {3}, {4}, {5}, {6}, {7}, {8}, {9}, {10}, {11}, " \
                                    "{12}, {13}, {14}, {15}, {16}, {17}, {18}, {19}, {20}, {21}, " \
                                    "{22}, {23}, {24}, {25}, {26}, '{27}', {28});".format(
                                            inv_table,
                                            ii,
                                            np.round(n, 2),
                                            np.round(cab, 2),
                                            np.round(car, 2),
                                            np.round(cbrown, 2),
                                            np.round(cw, 2),
                                            np.round(cm, 2),
                                            np.round(lai, 2),
                                            np.round(lidfa, 2),
                                            np.round(lidfb, 2),
                                            np.round(rsoil, 2),
                                            np.round(psoil, 2),
                                            np.round(hspot, 2),
                                            np.round(self.__tts, 4),
                                            np.round(self.__tto, 4),
                                            np.round(self.__psi, 4),
                                            np.round(typelidf, 2),
                                            np.round(sensor_spectrum[0], 4),
                                            np.round(sensor_spectrum[1], 4),
                                            np.round(sensor_spectrum[2], 4),
                                            np.round(sensor_spectrum[3], 4),
                                            np.round(sensor_spectrum[4], 4),
                                            np.round(sensor_spectrum[5], 4),
                                            np.round(sensor_spectrum[6], 4),
                                            np.round(sensor_spectrum[7], 4),
                                            np.round(sensor_spectrum[8], 4),
                                            self.acquisition_date,
                                            lc_code
                                            )
                try:
                    self.cursor.execute(insert_statement)
                    self.conn.commit()
                except (Exception, psycopg2.DatabaseError):
                    print("ERROR: INSERT of synthetic spectra failed!")
                    continue
            # endfor -> lut_table is finished


    def do_obj_inversion(self, object_id, acqui_date, land_use, num_solutions,
                         inv_params, res_table):
        """
        performs inversion per single object using mean of xx best
        solutions (RMSE criterion) and stores result results table
        params to be inverted/ returned should be passed as list of strings
        e.g: inv_params = ["LAI", "CAB"]
        also inverted spectra can be returned: therefore just append the band
        numbers to the list of strings of parameters:
        e.g. inv_params = ["LAI", "CAB", "B2", "B3", etc.]
        """
        query = """ SELECT 
                        lut.id,
                        rmse(obj.b2, obj.b3, obj.b4, obj.b5, obj.b6, obj.b7,
                             obj.b8a, obj.b11, obj.b12,lut.b2, lut.b3, lut.b4, 
                             lut.b5, lut.b6, lut.b7, lut.b8a, lut.b11, lut.b12) 
                        AS rmse
                    FROM
                        s2_obj_spec as obj,
                        s2_lut as lut
                    WHERE
                        obj.object_id = {0}
                    AND
                       obj.acquisition_date = '{1}'
                    AND
                       obj.landuse = {2}
                    AND
                        obj.landuse = lut.landuse
                    AND
                        obj.acquisition_date = lut.acquisition_date
                    ORDER BY rmse ASC
                    LIMIT {3};""".format(
                    object_id,
                    acqui_date,
                    land_use,
                    num_solutions)
        try:
            self.cursor.execute(query)
            inv_res = self.cursor.fetchall()
            lut_ids = [item[0] for item in inv_res]
            rmse_vals = [item[1] for item in inv_res]
            # convert lut_ids to str
            lut_ids = str(lut_ids)
            lut_ids = lut_ids.replace("[", "(")
            lut_ids = lut_ids.replace("]", ")")
            # convert the params to be inverted in the correct format
            # for SQL-query
            sql_snippets = []
            for param in inv_params:
                sql_snippet = "AVG(" + param + ")"
                sql_snippets.append(sql_snippet)
            # endfor
            sql_snippets = str(sql_snippets)
            sql_snippets = sql_snippets[1:len(sql_snippets)-1]
            sql_snippets = sql_snippets.replace("'", "")
            
            # select the biophysical parameters from the xx best solutions in the
            # lut table using the lut ids as keys
            query = "SELECT {0} FROM s2_lut WHERE id in {1};".format(
                    sql_snippets,
                    lut_ids)
            try:
                self.cursor.execute(query)
                mean_params = self.cursor.fetchall()
                # convert result to dictionary for storing results in DB
                result_dict = dict()
                
                index = 0
                for param in inv_params:
                    result_dict[param] = mean_params[0][index]
                    index += 1
                
                # also store the errors
                error_dict = dict()
                for ii in range(num_solutions):
                    error_dict[str(ii+1)] = rmse_vals[ii]
                
                # convert to json
                result_json = json.dumps(result_dict)
                error_json = json.dumps(error_dict)
                
                # insert statement
                insert = "INSERT INTO {0} VALUES ({1}, '{2}', '{3}', '{4}');".format(
                        res_table,
                        object_id,
                        acqui_date,
                        result_json,
                        error_json
                        )
                try:
                    self.cursor.execute(insert)
                    self.conn.commit()
                except Exception:
                    print("ERROR: Insert of results for object {0} failed!".format(
                            object_id))
                    return -1
            except Exception:
                print("Error: No inversion result could be obtained for object {0}".format(
                        object_id))
                return -1
        except Exception as err:
            
            print("ERROR: Inverting object with id {0} failed".format(object_id))
            print(err)
            return -1
        
        # return zero if everything was OK
        return 0
    # end function
    
    def do_inversion(self, acqui_date, land_use, num_solutions, res_table, return_specs=True):
        """
        performs inversion on all objects for a given date
        """
        # get list of objects available for a given land use class at a given day
        query = "SELECT DISTINCT object_id FROM s2_obj_spec " \
                " WHERE acquisition_date = '{0}'" \
                " AND landuse = {1};".format(
                        acqui_date,
                        land_use)
        try:
            self.cursor.execute(query)
            object_ids = self.cursor.fetchall()
            object_ids = [item[0] for item in object_ids]
            
        except Exception as err:
            print("ERROR: Could not query objects for acquistion date {0} and LUC {1}".format(
                    acqui_date,
                    land_use))
            print(err)
            sys.exit(-1)
        
        # get the list of params to be inverted
        query = "SELECT params_to_be_inverted FROM inversion_mapping" \
                " WHERE acquisition_date = '{0}' AND landuse = {1};".format(
                        acqui_date,
                        land_use
                        )
        
        try:
            self.cursor.execute(query)
            params = self.cursor.fetchall()
            params_dict = params[0][0]
            # convert to list
            params_list = []
            for key, val in params_dict.items():
                params_list.append(val)
            
            # if inverted spectra should be returned add them to params_list
            if (return_specs):
                band_names = ["B2", "B3", "B4", "B5", "B6", "B7", "B8A", "B11", "B12"]
                for band_name in band_names:
                    params_list.append(band_name)
                # endfor
            # endif
            
        except Exception as err:
            
            print("ERROR: Retrieving inversion metadata for acquisition date {0} and LUC {1} failed!".format(
                    acqui_date,
                    land_use))
            print(err)
            sys.exit(-1)
        
        # iterate over all objects to perform the inversion per object
        for ii in range(len(object_ids)):
            
            object_id = object_ids[ii]
            resrun = self.do_obj_inversion(object_id, acqui_date, land_use, 
                                           num_solutions, params_list, res_table)
            
            # in case an error happened
            if resrun != 0:
                self.conn.rollback()
                continue
            # endif
        # endfor
        
        # close database connection at the end
        if self.conn is not None:
            self.cursor.close()
            self.conn.close()
        # endif
    
    # end do_inversion
# end class
