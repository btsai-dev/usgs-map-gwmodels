"""
Code for preprocessing head observation data.
"""
import collections
import os
from pathlib import Path
import warnings
import numpy as np
import pandas as pd
from shapely.geometry import Point, MultiPolygon
import matplotlib.pyplot as plt
from gisutils import shp2df, df2shp, project, get_values_at_points
from mfsetup.obs import make_obsname
from mfsetup.units import convert_length_units
from mapgwm.lookups import aq_codes_dict, gwlevels_col_renames
from mapgwm.utils import makedirs, assign_geographic_obsgroups, cull_data_to_active_area


def get_header_length(sitefile, col0='SITE_BADGE'):
    """Detect the number of rows to skip when reading
    a tabular file with '#' head comments.
    """
    with open(sitefile) as src:
        for i, line in enumerate(src):
            if '#' not in str(line) and col0 in str(line):
                return i


def read_metadata(metadata_files, column_renames=None,
                  aquifer_names=None,
                  metadata_kwargs={}):
    """Read groundwater level metadata output from the
    `visGWDB program <https://doi.org/10.5066/P9W004O6>`_.

    Parameters
    ----------

    metadata_files : str
        Text file with groundwater level metadata. Expected to have
        the following columns, or their renames. See the :ref:`Head Observation Input`
        for more details.
        (NAME: rename):

        .. literalinclude:: ../../../mapgwm/lookups/gwlevels_column_renames.yml
            :language: yaml
            :start-after: # metadata column

    aquifer_names : dict, optional
        Option to explicitly specify regional aquifer names associated with local aquifer codes.
        Example::

            aquifer_names={'124SPT': 'middle clairborne'}

        by default None, in which case the names listed in the :ref:`Regional Aquifer Code Names` lookup
        are used.

    column_renames : dict, optional
        Option to rename columns in the data or metadata that are different than those listed above.
        For example, if the data file has a 'SITE_NO' column instead of 'SITE_BADGE'::

            column_renames={'SITE_NO': 'site_no'}

        by default None, in which case the renames listed above will be used.
        Note that the renames must be the same as those listed above for
        :func:`mapgwm.headobs.preprocess_headobs` to work.

    metadata_kwargs : dict, optional
        Keyword arguments to :func:`pandas.read_csv` for reading ``metadata_files``,
        by default None

    Returns
    -------
    metadata : DataFrame
        Head observation metadata, e.g. for input to func:`mapgwm.headobs.preprocess_headobs`

        Key columns:

        ================= ==========================================================================
        site_no (index)   site identifier
        aqfr_cd           Local aquifer code
        nat_aqfr_cd       National aquifer code
        screen_botm       Well screen bottom, as a depth below land surface, in feet
        screen_top        Well screen top, as a depth below land surface, in feet
        well_depth        Well depth, in feet
        well_el           Altitude of land surface, in feet
        local_aquifer     Local aquifer name corresponding to aqfr_cd, see :ref:`Aquifer Code Names`
        regional_aquifer  Lumped regional aquifer, see :ref:`Regional Aquifer Code Names`
        ================= ==========================================================================
    """
    # update the default column renames
    # with any supplied via column_renames parameter
    col_renames = gwlevels_col_renames.copy()
    if isinstance(column_renames, collections.Mapping):
        col_renames.update(column_renames)

    # read in the metadata
    dflist = []
    if isinstance(metadata_files, str) or isinstance(metadata_files, Path):  # allows list of paths or single path
        metadata_files = [metadata_files]
    # default columns to read, unless 'usecols' is specified in the metadata kwargs
    metadata_usecols = [
        'SITE_BADGE',
        'WELL_DEPTH_VA',
        'OPEN_TOP_VA',
        'OPEN_BOTTOM_VA',
        'AQFR_CD',
        'NAT_AQFR_CD',
        'ALT_VA'
    ]
    if 'usecols' in metadata_kwargs:
        metadata_usecols = metadata_kwargs.pop('usecols')
    for f in metadata_files:
        metadata_skiprows = get_header_length(f)
        df = pd.read_csv(f, usecols=metadata_usecols, sep='\t',
                         skiprows=metadata_skiprows)
        df.rename(columns=col_renames, inplace=True)
        df.set_index('site_no', inplace=True)
        df.columns = df.columns.str.lower()
        dflist.append(df)
    metadata = pd.concat(dflist, sort=True)

    # create aquifer column in metadata
    regional_aquifer_codes = aq_codes_dict['regional_aquifer'].copy()
    if isinstance(aquifer_names, collections.Mapping):
        regional_aquifer_codes.update(aquifer_names)
    metadata['local_aquifer'] = [aq_codes_dict['aquifer_code_names'].get(cd, 'unspecified')
                                 for cd in metadata.aqfr_cd]
    metadata['regional_aquifer'] = [regional_aquifer_codes.get(cd, 'unspecified')
                                    for cd in metadata.aqfr_cd]
    unspec_rows, _ = np.where(metadata[['local_aquifer', 'regional_aquifer']] == 'unspecified')
    if any(unspec_rows):
        codes_without_names = set(metadata['aqfr_cd'].iloc[unspec_rows])
        warnings.warn(('No aquifer names found for aquifer codes:\n{}\n'
                       'Please add them to mapgwm/lookups/aquifer_codes.yml, '
                       'or specify them with the aquifer_names argument'.format(codes_without_names)))
    return metadata

            
def get_data(data_file, metadata_files, aquifer_names=None,
             column_renames=None,
             data_kwargs={}, metadata_kwargs={}):
    """Read groundwater level data output from the 
    `visGWDB program <https://doi.org/10.5066/P9W004O6>`_.

    Parameters
    ----------
    data_file : str
        Text file with groundwater level time series. Expected to have
        the following columns, or their renames. See the :ref:`Head Observation Input`
        for more details.
        (NAME: rename):
        
        .. literalinclude:: ../../../mapgwm/lookups/gwlevels_column_renames.yml
            :language: yaml
            :start-after: # data columns
            :end-before: # metadata column
            
    metadata_files : str
        Text file with groundwater level metadata. Expected to have
        the following columns, or their renames. See the :ref:`Head Observation Input`
        for more details.
        (NAME: rename):
        
        .. literalinclude:: ../../../mapgwm/lookups/gwlevels_column_renames.yml
            :language: yaml
            :start-after: # metadata column
            
    aquifer_names : dict, optional
        Option to explicitly specify regional aquifer names associated with local aquifer codes.
        Example::

            aquifer_names={'124SPT': 'middle clairborne'}

        by default None, in which case the names listed in the :ref:`Regional Aquifer Code Names` lookup
        are used.

    column_renames : dict, optional
        Option to rename columns in the data or metadata that are different than those listed above.
        For example, if the data file has a 'SITE_NO' column instead of 'SITE_BADGE'::

            column_renames={'SITE_NO': 'site_no'}

        by default None, in which case the renames listed above will be used.
        Note that the renames must be the same as those listed above for
        :func:`mapgwm.headobs.preprocess_headobs` to work.

    data_kwargs : dict, optional
        Keyword arguments to :func:`pandas.read_csv` for reading ``data_file``,
        by default None
    metadata_kwargs : dict, optional
        Keyword arguments to :func:`pandas.read_csv` for reading ``metadata_files``,
        by default None

    Returns
    -------
    data : DataFrame
        Head observation timeseries, e.g. for input to func:`mapgwm.headobs.preprocess_headobs`

        Key columns:

        ========= ================================================================
        site_no   site identifier
        lat       lattitude
        lon       longitude
        datetime  measurement dates in pandas datetime format
        head      average head for the period represented by the datetime
        last_head last head measurement for the period represented by the datetime
        head_std  standard deviation of measured heads within the datetime period
        n         number of measured heads within the period represented
        ========= ================================================================

    metadata : DataFrame
        Head observation metadata, e.g. for input to func:`mapgwm.headobs.preprocess_headobs`

        Key columns:

        ================= ==========================================================================
        site_no (index)   site identifier
        aqfr_cd           Local aquifer code
        nat_aqfr_cd       National aquifer code
        screen_botm       Well screen bottom, as a depth below land surface, in feet
        screen_top        Well screen top, as a depth below land surface, in feet
        well_depth        Well depth, in feet
        well_el           Altitude of land surface, in feet
        local_aquifer     Local aquifer name corresponding to aqfr_cd, see :ref:`Aquifer Code Names`
        regional_aquifer  Lumped regional aquifer, see :ref:`Regional Aquifer Code Names`
        ================= ==========================================================================

    """
    # update the default column renames
    # with any supplied via column_renames parameter
    col_renames = gwlevels_col_renames.copy()
    if isinstance(column_renames, collections.Mapping):
        col_renames.update(column_renames)

    data_skiprows = get_header_length(data_file)
    data = pd.read_csv(data_file, sep='\t',
                       skiprows=data_skiprows, **data_kwargs)
    data.rename(columns=col_renames, inplace=True)
    data.columns = data.columns.str.lower()
    if 'datetime' not in data.columns:
        datetimes = ['{}-{:02d}'.format(year, month) 
                     for year, month in zip(data.year, data.month)]
        data['datetime'] = pd.to_datetime(datetimes)
    else:
        data['datetime'] = pd.to_datetime(data['datetime'])
    
    # read in the metadata
    metadata = read_metadata(metadata_files, column_renames=col_renames,
                             aquifer_names=aquifer_names,
                             metadata_kwargs=metadata_kwargs)
    return data, metadata


def preprocess_headobs(data, metadata, head_data_columns=['head', 'last_head', 'head_std'],
                       dem=None, dem_units='meters',
                       start_date='1998-04-01', active_area=None,
                       active_area_id_column=None,
                       active_area_feature_id=None,
                       source_crs=4269, dest_crs=5070,
                       data_length_units='meters',
                       model_length_units='meters',
                       geographic_groups=None,
                       geographic_groups_col=None,
                       max_obsname_len=None,
                       outfile='../source_data/observations/head_obs/preprocessed_head_obs.csv'):

    """Preprocess head observation data, for example, groundwater level data output from the
    `visGWDB program <https://doi.org/10.5066/P9W004O6>`_.

    * Data are reprojected from a `source_crs` (Coordinate reference system; assumed to be in geographic coordinates)
      to the CRS of the model (`dest_crs`)
    * Data are culled to a `start_date` and optionally, a polygon or set of polygons defining the model area
    * length units are converted to those of the groundwater model. Open intervals for the wells are
      converted from depths to elevations
    * missing open intervals are filled based on well bottom depths (if availabile) and the median open
      interval length for the dataset.
    * Wells are categorized based on the quality of the open interval information (see the documentation
      for :func:`mapgwm.headobs.fill_well_open_intervals`).
    * Prefixes for observation names (with an optional length limit) that identify the location are generated
    * Preliminary observation groups can also be assigned, based on geographic areas defined by polygons
      (`aoi` parameter)

    Parameters
    ----------
    data : DataFrame
        Head observation data, e.g. as output from :func:`mapgwm.headobs.get_data`.
        Columns:

        ========= ================================================================
        site_no   site identifier
        lat       lattitude
        lon       longitude
        datetime  measurement dates in pandas datetime format
        head      average head for the period represented by the datetime
        last_head last head measurement for the period represented by the datetime
        head_std  standard deviation of measured heads within the datetime period
        ========= ================================================================

        Notes:

        * lat and lon columns can alternatively be in the metadata table
        * `last_head` and `head_std` only need to be included if they are in
          `head_data_columns`

    metadata : DataFrame
        Head observation data, e.g. as output from :func:`mapgwm.headobs.get_data`.

        Must have the following columns:

        ================= ==========================================================================
        site_no (index)   site identifier
        aqfr_cd           Local aquifer code
        screen_botm       Well screen bottom, as a depth below land surface, in feet
        screen_top        Well screen top, as a depth below land surface, in feet
        well_depth        Well depth, in feet
        well_el           Altitude of land surface, in feet
        ================= ==========================================================================

    head_data_columns : list of strings
        Columns in data with head values or their statistics.
        By default, 'head', 'last_head', 'head_std', which allows both
        the average and last head values for the stress period to be considered,
        as well as the variability of water levels contributing to an average value.
    dem : str, optional
        DEM raster of the land surface. Used for estimating missing wellhead elevations.
        Any reprojection to dest_crs is handled automatically, assuming
        the DEM raster has CRS information embedded (arc-ascii grids do not!)
        By default, None.
    dem_units : str, {'feet', 'meters', ..}
        Units of DEM elevations, by default, 'meters'
    start_date : str (YYYY-mm-dd)
        Simulation start date (cull observations before this date)
    active_area : str
        Shapefile with polygon to cull observations to. Automatically reprojected
        to dest_crs if the shapefile includes a .prj file.
        by default, None.
    active_area_id_column : str, optional
        Column in active_area with feature ids.
        By default, None, in which case all features are used.
    active_area_feature_id : str, optional
        ID of feature to use for active area
        By default, None, in which case all features are used.
    source_crs : obj
        Coordinate reference system of the head observation locations.
        A Python int, dict, str, or :class:`pyproj.crs.CRS` instance
        passed to :meth:`pyproj.crs.CRS.from_user_input`

        Can be any of:
          - PROJ string
          - Dictionary of PROJ parameters
          - PROJ keyword arguments for parameters
          - JSON string with PROJ parameters
          - CRS WKT string
          - An authority string [i.e. 'epsg:4326']
          - An EPSG integer code [i.e. 4326]
          - A tuple of ("auth_name": "auth_code") [i.e ('epsg', '4326')]
          - An object with a `to_wkt` method.
          - A :class:`pyproj.crs.CRS` class

        By default, epsg:4269

    dest_crs : obj
        Coordinate reference system of the model. Same input types
        as ``source_crs``.
        By default, epsg:5070
    data_length_units : str; 'meters', 'feet', etc.
        Length units of head observations.
    model_length_units : str; 'meters', 'feet', etc.
        Length units of model.
    geographic_groups : file, dict or list-like
        Option to group observations by area(s) of interest. Can
        be a shapefile, list of shapefiles, or dictionary of shapely polygons.
        A 'group' column will be created in the metadata, and observation
        sites within each polygon will be assigned the group name
        associated with that polygon.

        For example::

            geographic_groups='../source_data/extents/CompositeHydrographArea.shp'
            geographic_groups=['../source_data/extents/CompositeHydrographArea.shp']
            geographic_groups={'cha': <shapely Polygon>}

        Where 'cha' is an observation group name for observations located within the
        the area defined by CompositeHydrographArea.shp. For shapefiles,
        group names are provided in a `geographic_groups_col`.

    geographic_groups_col : str
        Field name in the `geographic_groups` shapefile(s) containing the
        observation group names associated with each polygon.

    max_obsname_len : int or None
        Maximum length for observation name prefix. Default of 13
        allows for a PEST obsnme of 20 characters or less with
        <prefix>_yyyydd or <prefix>_<per>d<per>
        (e.g. <prefix>_2d1 for a difference between stress periods 2 and 1)
        If None, observation names will not be truncated. PEST++ does not have
        a limit on observation name length.
    outfile : str
        Where output file will be written. Metadata are written to a file
        with the same name, with an additional "_info" suffix prior to
        the file extension.

    Returns
    -------
    df : DataFrame
        Preprocessed time series
    well_info : DataFrame
        Preprocessed metadata

    References
    ----------
    `The PEST++ Manual <https://github.com/usgs/pestpp/tree/master/documentation>`
    """

    df = data.copy()
    # multiplier to convert input length units to model units
    unit_conversion = convert_length_units(data_length_units, model_length_units)

    # outputs
    outpath, filename = os.path.split(outfile)
    makedirs(outpath)
    outname, ext = os.path.splitext(outfile)
    out_info_csvfile = outname + '_info.csv'
    out_data_csvfile = outfile
    out_plot = os.path.join(outpath, 'open_interval_lengths.pdf')
    out_shapefile = outname + '_info.shp'

    # set the starting and ending dates here
    stdate = pd.Timestamp(start_date)

    # convert to datetime; drop the timestamps
    df['datetime'] = pd.to_datetime(df.datetime).dt.normalize()

    # trim to the time range
    print('starting with {} unique wells'.format(len(set(data.site_no))))
    no_data_in_period = df.datetime < stdate
    if np.any(no_data_in_period):
        print(('culling {} wells with only data prior to start date of {}'
               .format(np.sum(df.datetime < stdate), stdate)))
        df = df.loc[(df.datetime >= stdate)]

    # collapse dataset to mean values at each site
    groups = df.groupby('site_no')
    well_info = groups.mean().copy()
    well_info = well_info.join(metadata, rsuffix='_meta')
    well_info['start_dt'] = groups.datetime.min()
    well_info['end_dt'] = groups.datetime.max()
    well_info.drop(labels=['year', 'month'], axis=1, inplace=True)
    well_info['site_no'] = well_info.index

    # project x, y to model crs
    x_pr, y_pr = project((well_info.lon.values, well_info.lat.values), source_crs, dest_crs)
    well_info.drop(['lon', 'lat'], axis=1, inplace=True)
    well_info['x'], well_info['y'] = x_pr, y_pr
    well_info['geometry'] = [Point(x, y) for x, y in zip(x_pr, y_pr)]

    # cull data to that within the model area
    if active_area is not None:
        df, md = cull_data_to_active_area(df, active_area,
                                          active_area_id_column,
                                          active_area_feature_id,
                                          data_crs=dest_crs, metadata=well_info)

    # convert length units; convert screen tops and botms to depths
    missing_elevations = well_info.well_el.isna()
    if dem is not None and np.any(missing_elevations):
        well_location_elevations = get_values_at_points(dem, well_info['x'], well_info['y'], points_crs=dest_crs)
        well_location_elevations *= convert_length_units(dem_units, model_length_units)
        well_info.loc[missing_elevations, 'well_el'] = well_location_elevations[missing_elevations]

    length_columns = ['well_el'] + head_data_columns + ['screen_top', 'screen_botm']
    for col in length_columns:
        if col in well_info.columns:
            well_info[col] *= unit_conversion

    well_info['well_botm'] = well_info['well_el'] - well_info['well_depth']
    well_info['screen_top'] = well_info['well_el'] - well_info['screen_top']
    well_info['screen_botm'] = well_info['well_el'] - well_info['screen_botm']

    # just the data, site numbers, times and aquifer
    head_data_columns = head_data_columns + ['head_std']
    transient_cols = ['site_no', 'datetime'] + head_data_columns + ['n']
    transient_cols = [c for c in transient_cols if c in df.columns]
    df = df[transient_cols].copy()
    for c in head_data_columns:
        if c in df.columns:
            df[c] *= unit_conversion

    # #### trim down to only well_info with both estimated water levels and standard deviation
    # monthly measured levels may not have standard deviation
    # (as opposed to monthly statistical estimates)
    criteria = pd.notnull(well_info['head'])
    #if 'head_std' in df.columns:
    #    criteria = criteria & pd.notnull(well_info['head_std'])
    well_info = well_info[criteria]

    # verify that all well_info have a wellhead elevation
    assert not np.any(np.isnan(well_info.well_el))

    # categorize wells based on quality of open interval information
    # estimate missing open intervals where possible
    well_info = fill_well_open_intervals(well_info, out_plot=out_plot)

    # drop well_info with negative reported open interval
    #well_info = well_info.loc[open_interval_length > 0]

    # cull data to well_info in well info table
    has_metadata = df.site_no.isin(well_info.index)
    if np.any(~has_metadata):
        warnings.warn('culling {} wells not found in metadata table!'
                      .format(np.sum(~has_metadata)))
        df = df.loc[has_metadata].copy()

    # make unique n-character prefixes (site identifiers) for each observation location
    # 13 character length allows for prefix_yyyymmm in 20 character observation names
    # (BeoPEST limit)
    unique_obsnames = set()
    obsnames = []
    for sn in well_info.index.tolist():
        if max_obsname_len is not None:
            name = make_obsname(sn, unique_names=unique_obsnames,
                                maxlen=max_obsname_len)
            assert name not in unique_obsnames
        else:
            name = sn
        unique_obsnames.add(name)
        obsnames.append(name)
    well_info['obsprefix'] = obsnames
    obsprefix = dict(zip(well_info.index, well_info.obsprefix))
    df['obsprefix'] = [obsprefix[sn] for sn in df.site_no]

    # add area of interest information
    well_info['group'] = 'heads'
    well_info = assign_geographic_obsgroups(well_info, geographic_groups,
                                            geographic_groups_col,
                                            metadata_crs=dest_crs)

    # save out the results
    df2shp(well_info.drop(['x', 'y'], axis=1),
           out_shapefile, index=False, crs=dest_crs)
    print('writing {}'.format(out_info_csvfile))
    well_info.drop('geometry', axis=1).to_csv(out_info_csvfile, index=False, float_format='%.2f')
    print('writing {}'.format(out_data_csvfile))
    df.to_csv(out_data_csvfile, index=False, float_format='%.2f')
    return df, well_info


def fill_well_open_intervals(well_info, out_plot='open_interval_lengths.pdf'):
    """Many or most of the well_info output from `visGWDB <https://doi.org/10.5066/P9W004O6>`
    may not have complete open interval information. A much greater proportion may have
    depth information. Use reported well depths and a computed median open interval
    length to estimate a screen top and bottom where available. Categorize well_info
    based on the quality of open interval information:

    1) top and bottom elevations included
    2) only bottom (median open interval length of 40 ft. used for top)
    3) bottom and well depth are inconsistent
    4) neither top or bottom are known

    Parameters
    ----------
    well_info : DataFrame
        Metadata from :func:`~mapgwm.headobs.preprocess_headobs`
        Columns (all lengths in model units):

        ===========  ====================================
        screen_top   open interval top elevation
        screen_botm  open interval bottom elevation
        well_botm    well bottom elevation
        ===========  ====================================

    Returns
    -------
    well_info : DataFrame
        Metadata from :func:`~mapgwm.headobs.preprocess_headobs`, with
        additional screen top and bottom estimates and category column
        with above categories.
    """

    is_incomplete = well_info['screen_top'].isna() | well_info['screen_botm'].isna()
    print(('{:.0%} of wells (n={}) have incomplete open interval information. '
           'Filling screen bottoms with well bottoms where available, '
           'and estimating screen tops from median open interval length.'
           .format(is_incomplete.sum()/len(well_info), is_incomplete.sum())))

    # populate a well bottom column from well depth or open bottom field(s)
    bottom_known = ~np.isnan(well_info['well_botm']) | ~np.isnan(well_info['screen_botm'])
    well_info['botm'] = well_info['screen_botm'].values
    # only use well depth where there isn't a screen botm
    botm_from_well_depth = np.isnan(well_info['botm']) & bottom_known
    well_info.loc[botm_from_well_depth, 'botm'] = well_info.loc[botm_from_well_depth, 'well_botm']

    # label indicating what is known about open interval
    # 1) top and bottom elevations included
    # 2) only bottom (use median open interval length of 40 ft. for top)
    # 3) bottom and well depth are inconsistent
    # 4) neither top or bottom are known

    well_info['category'] = 4
    cd2 = bottom_known & np.isnan(well_info['screen_top'])
    cd1 = bottom_known & ~np.isnan(well_info['screen_top'])
    well_info.loc[cd2, 'category'] = 2
    well_info.loc[cd1, 'category'] = 1

    # identify well_info where well depth and open interval bottom are different
    tol = 1
    inds = ~np.isnan(well_info['well_botm']) & ~np.isnan(well_info['screen_botm'])
    isdifferent = np.abs(well_info.loc[inds, 'well_botm'] - well_info.loc[inds, 'screen_botm']) > tol
    well_info.loc[inds & isdifferent, 'category'] = 3

    # group 1 well_info have known tops and bottoms
    well_info['top'] = well_info['screen_top'].values

    # assign group 2 tops using median open interval length
    # these well_info only have well_depth values
    # make a histogram of open interval length
    open_interval_length = well_info['screen_top'] - well_info['screen_botm']
    ax = open_interval_length.hist(bins=100)
    ax.set_xlabel('Well open interval length, in feet')
    ax.set_ylabel('Count')
    ax.text(.3, .7, 'median: {:.0f}\nmean: {:.0f}\nmax: {:.0f}\nmin:  {:.0f}'.format(open_interval_length.median(),
                                                                                     open_interval_length.mean(),
                                                                                     open_interval_length.max(),
                                                                                     open_interval_length.min()
                                                                                     ), transform=ax.transAxes)
    plt.savefig(out_plot)
    plt.close()
    median = open_interval_length.median()
    ind = well_info.category == 2
    well_info.loc[ind, 'top'] = well_info.loc[ind, 'well_botm'] + median
    well_info.loc[ind, 'botm'] = well_info.loc[ind, 'well_botm']

    # verify that the assigned top and bottom depths are consistent with
    # original columns
    diff = (well_info.botm - well_info.screen_botm).abs()
    diff2 = (well_info.botm - well_info.well_botm).abs()
    ind = ~np.isnan(well_info.screen_botm)
    ind2 = ~np.isnan(well_info.well_depth)
    assert diff[ind].sum() == 0
    # verify that assigned botms are at most tol feet different
    # from well depth values
    #assert diff2[ind].max() <= tol

    # replace the original screen top/botm columns with filled versions
    # maintain original values for checking
    well_info['orig_scbot'] = well_info['screen_botm']
    well_info['orig_sctop'] = well_info['screen_top']
    well_info['screen_botm'] = well_info['botm']
    well_info['screen_top'] = well_info['top']
    well_info.drop(labels=['botm', 'top'], axis=1, inplace=True)

    is_incomplete = well_info['screen_top'].isna() | well_info['screen_botm'].isna()
    print('{:.0%} of wells (n={}) still missing open interval information; '
          'assigned to category 4.'.format(is_incomplete.sum()/len(well_info),
                                           is_incomplete.sum()))
    # any wells still missing information should be in the fourth category
    assert set(well_info.loc[is_incomplete, 'category']).union({4}) == {4} == {4}

    return well_info