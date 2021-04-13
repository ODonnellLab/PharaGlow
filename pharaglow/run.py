#!/usr/bin/env python

"""run.py: run pharaglow analysis by inplace modifying pandas dataframes."""

import numpy as np
from skimage.io import imread
from skimage import util
import json
import pandas as pd

import pharaglow.features as pg
import pharaglow.tracking as pgt
import pharaglow.util as pgu


def runPharaglowSkel(im):
    """create a centerline of the object in the image by binarizing, skeletonizing and sorting centerline points.
        Inputs:
            im: image
            length: length of one axis of the image
        Outputs:
            mask: binary of image, unraveled
            ptsX: coordinates of centerline along X
            ptsY: coordinates of centerline along Y
    """
    
    mask = pg.thresholdPharynx(im)
    skel = pg.skeletonPharynx(mask)
    order = pg.sortSkeleton(skel)
    ptsX, ptsY = np.where(skel)
    ptsX, ptsY = ptsX[order], ptsY[order]
    return mask.ravel(), ptsX, ptsY


def runPharaglowCL(mask, ptsX, ptsY, length, nPts = 100):
    """Fit centerline points and detect object morphology.
            mask: binary image, unraveled
            ptsX: coordinates of centerline along X
            ptsY: coordinates of centerline along Y
            length: length of one axis of the image
        Outputs:
            poptX, poptY, xstart, xend: parameters describing a functon along the centerline
            cl: (n,2) list of centerline coordinates in image space.
            dCl: (n,2) array of unit vectors orthogonal to centerline. Same length as cl.
            widths: (n,2) widths of the contour at each centerline point.
            contour: (m,2) coordinates along the contour of the object
    """
    # mask reshaping from linear
    mask = pgu.unravelImages(mask, length)
    # getting centerline and widths along midline
    poptX, poptY = pg.fitSkeleton(ptsX, ptsY)
    contour = pg.morphologicalPharynxContour(mask, scale = 4, smoothing=2)
    xstart, xend = pg.cropcenterline(poptX, poptY, contour, nP = len(ptsX))
    # extend a bit to include all bulbs
    #l = np.abs(xstart-xend)
    #xstart -= 0.05*l
    #xend += 0.05*l
    # make sure centerline isn't too short if something goes wrong
    if np.abs(xstart-xend) < 0.5*len(ptsX):
        xstart, xend = 0, len(ptsX)
    xs = np.linspace(xstart, xend, nPts)
    cl = pg.centerline(poptX, poptY, xs)
    dCl = pg.normalVecCl(poptX, poptY, xs)
    widths = pg.widthPharynx(cl, contour, dCl)
    if (np.argmax(pg.scalarWidth(widths)) - 0.5*len(widths)) <= 0:
        xtmp = xstart
        xstart = xend
        xend = xtmp
        cl = cl[::-1]
        dCl = dCl[::-1]
        widths = widths[::-1]
    return poptX, poptY, xstart, xend, cl, dCl, widths, contour


def runPharaglowKymo(im, cl, widths, **kwargs):
    """Use the centerline to extract intensity along this line from an image.
       Inputs:
            im: image
            cl: (n,2) list of centerline coordinates in image space.
            widths: scalar width along centerline (n,2)
        Outputs:
            intensity (N,): array of pixel intensities
    """
    
    #kymoWeighted = pg.intensityAlongCenterline(im, cl, width = pg.scalarWidth(widths))[:,0]
    return [pg.intensityAlongCenterline(im, cl, **kwargs)]


def runPharaglowImg(im, xstart, xend, poptX, poptY, width, npts):
    """Obtain the straightened version and gradient of the input image.
        Inputs:
            im: an image
            xstart, xend, poptX, poptY are the parameters of a curve/centerline describing the shape of the pharynx
            width: how far to sample left and right of the centerline
            npts: how any points to sample along the centerline
        Outputs:
            gradientimage: local derivative of image
            straightIm:  (nPts, width) array of image intensity
            
    """
    
    #local derivative, can enhance contrast
    gradientImage = pg.gradientPharynx(im)
    # straightened image
    straightIm = pg.straightenPharynx(im, xstart, xend, poptX, poptY, width=width, nPts = npts)
    return gradientImage, straightIm


def pharynxorientation(df):
    """A Get the orientation from the minimal trajectory of the start and end points for a single trajectory."""
    df.loc[:,'StraightKymo'] = df.apply(
    lambda row: np.mean(row['Straightened'], axis = 1), axis=1)
    df['Similarity'] = False
    sample = df['StraightKymo'].mean()
    # let's make sure the sample is anterior to posterior
    if np.mean(sample[:len(sample//2)])>np.mean(sample[len(sample//2):]):
        sample = sample[::-1]
    # this uses the intenisty profile - sometimes goes wrong
    df['Similarity'] = df.apply(\
        lambda row: np.sum((row['StraightKymo']-sample)**2)<np.sum((row['StraightKymo']-sample[::-1])**2), axis=1)
    # now flip the orientation where the sample is upside down
    for key in ['SkeletonX', 'SkeletonY', 'Centerline', 'dCl', 'Widths', 'Kymo',\
           'StraightKymo', 'Straightened', 'KymoGrad']:
        if key in df.columns:
            df[key] = df.apply(lambda row: row[key] if row['Similarity'] else row[key][::-1], axis=1)
    # Flip the start coordinates
    if set(['Xstart', 'Xend']).issubset(df.columns):
        df['Xtmp'] = df['Xstart']
        df['Xstart'] = df.apply(lambda row: row['Xstart'] if row['Similarity'] else row['Xend'], axis=1)
        df['Xend'] = df.apply(lambda row: row['Xend'] if row['Similarity'] else row['Xtmp'], axis=1)
    return df


def runPharaglowOnImage(image, framenumber, params, **kwargs):
    """"run pharaglow-specific image analysis on a single image."""
    if 'run_all' in kwargs.keys():
        run_all = kwargs['run_all']
    else:
        run_all = False
    colnames = ['Mask', 'SkeletonX', 'SkeletonY','ParX', 'ParY', 'Xstart', 'Xend', 'Centerline', 'dCl', 'Widths', \
        'Contour','Gradient', 'Straightened', 'Kymo', 'KymoGrad']
    
    # skeletonize
    mask, skelX, skelY = runPharaglowSkel(image)
    #centerline fit
    parX, parY, xstart, xend, cl, dCl, widths, contour = runPharaglowCL(mask,skelX, skelY, params['length'])
    # image transformation operations
    grad, straightened = runPharaglowImg(image, xstart,xend,\
                                            parX, parY, params['widthStraight'],\
                                            params['nPts'])
    results = [mask, skelX, skelY, parX, parY, xstart, xend, cl, dCl, widths, contour, grad, straightened]
    if run_all:
        # run kymographs
        kymo= runPharaglowKymo(image, cl, widths, linewidth = params['linewidth'])
        # run kymographs
        kymograd = runPharaglowKymo(grad, cl, widths, linewidth = params['linewidth'])
        results.append(kymo, kymograd)
    data = {}
    for col, res in zip(colnames,results):
        data[col] = res
    df = pd.DataFrame([data], dtype = 'object')
    df['frame'] = framenumber
    return df, 


# def runPharaglowOnStack(image, index, param, run_all = True):
#     """runs the whole pharaglow toolset on an image stack."""
#     colnames = ['Mask', 'SkeletonX', 'SkeletonY','ParX', 'ParY', 'Xstart', 'Xend', 'Centerline', 'dCl', 'Widths', \
#         'Contour','Gradient', 'Straightened', 'Kymo', 'KymoGrad']
#     data = []
#     df = pd.DataFrame(dtype = 'object')
#     for idx in index:
#         image = images[idx]
#         if np.sum(image)==0:
#             df.drop(idx)
#             data.append(runPharaglowOnImage(image, param, run_all))
#     if run_all:
#         df[[colnames]] = data
#     else:
#         df[[colnames[:-2]]] = data
#     df.index = index
#     # extract pumping metric
#     df[['pumps']] = df.apply(\
#         lambda row: pd.Series(pg.extractPump(row['Straightened'])), axis=1)
#     return df, images


def parallel_pharaglow_run(args, **kwargs):
    """define a worker function for parallelization."""
    return runPharaglowOnImage(*args, **kwargs)

        

