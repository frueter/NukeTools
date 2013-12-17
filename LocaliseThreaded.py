import filecmp
import nuke
import os
import re
import shutil
import threading
from PySide import QtCore

# Mimic nuke.localiseFile but make it threaded so it can run in the background
# 
# WARNING:
# This does not implement the preferences' disk cache size knob yet!!!
#
#
# To install duck punch nuke.localiseFiles like this:
#    import LocaliseThreaded
#    nuke.localiseFilesHOLD = nuke.localiseFiles #BACKUP ORIGINAL
#    nuke.localiseFiles = LocaliseThreaded.localiseFileThreaded
#    doLocalise(True) # ONLY FOR DEBUGGING, THIS IS ACTUALLY CALLED FROM THE DEFAULT MENU
     

class LocaliseThreaded(object):
    # TO DO:
    # -thread manager to be able to set maximum threads and split one node into multiple threads if max is not used
    # -stereo images if file knob is split

    def __init__(self, fileDict, maxThreads=1):
        '''
        Threaded interface for copying files
        fileDict  -  dictionary where key is the name of the sequence (used for progress bar) and value is a list of files to be copied
        '''
        self.fileDict = fileDict
        self.cachePath = nuke.value('preferences.localCachePath')
        self.taskCount = len(self.fileDict)
        self.totalFileCount = sum([len(v) for v in self.fileDict.values()])
        self.progress = 0.0
        self.cachePath = nuke.value('preferences.localCachePath')
        self.finishedThreads = 0
        self.threadLimit = int(nuke.THREADS / 2.0) # SHOULD MAKE THIS A PREFERENCES
        self.threadLimiter = threading.BoundedSemaphore(self.threadLimit)


    def start(self):
        '''start copying files'''
        self.mainTask = nuke.ProgressTask('LOCALISING %s files' % self.totalFileCount)
        self.__updateMainTaskMessage()
        for seqName, fileList in self.fileDict.iteritems():
            thread = threading.Thread(name=seqName, target=self.copyFiles, args=(seqName, fileList))
            thread.start()

    def copyFiles(self, taskName, fileList):
        '''Copy all files'''
        self.threadLimiter.acquire()
        task = nuke.ProgressTask('%s (%s files)' % (taskName, len(fileList)))
        for i, filePath in enumerate(fileList):
            if task.isCancelled() or self.mainTask.isCancelled():
                break
            # COPY FILE
            #print 'copying %s to %s' % (filePath, self.getTargetDir(filePath))
            self.copyFile(filePath, self.getTargetDir(filePath))
            # UPDATE LOCAL TASK
            task.setMessage('localising %s' % filePath)
            task.setProgress(int(float(i) / len(fileList) * 100))
            # UPDATE GLOBAL TASK
            self.progress += 1
            self.mainTask.setProgress(int(self.progress / self.totalFileCount * 100))
        self.reportFinishedThread()
        self.threadLimiter.release()


    def reportFinishedThread(self):
        '''Used to update the main task message and invoke the indicator on all nodes after localisation finsishes'''
        print threading.currentThread().getName(), 'FINISHED'
        self.finishedThreads += 1
        self.__updateMainTaskMessage()
        if self.finishedThreads == self.taskCount:
            self.__forceUpdate()

    def __updateMainTaskMessage(self):
        self.mainTask.setMessage('%s/%s concurrent tasks' % (self.finishedThreads, self.taskCount))

    def __forceUpdate(self):
        '''Silly workaround to update the node indicators. node.update() doesn't do the trick'''
        n = nuke.nodes.NoOp()
        nuke.delete(n)

    def copyFile(self, filePath, destPath):
        '''
        Copy filePath to destPath. destPath will be created if it doesn't exist.
        filePath will not be copied if the file already exists in destPath unless the local copy has an older time stamp
        '''
        if not os.path.isdir(destPath):
            os.makedirs(destPath)
        localFile = os.path.join(destPath, os.path.basename(filePath))

        if not os.path.isfile(localFile):
            # FILE DOES NOT EXISTS LOCALLY - COPY IT
            print 'copying new local file'
            shutil.copy2(filePath, destPath)
        elif not filecmp.cmp(filePath, localFile):
            # LOCAL COPY SEEMS OUT OF SYNC - COPY IT AGAIN
            print 'updating local file'
            shutil.copy2(filePath, destPath)
        else:
            # LOCAL FILE IS UP-TO-DATE - NOTHING TO DO
            print 'doing nothing'
            pass
        

    def getTargetDir(self, filePath):
        '''Get the target directory for filePath based on Nuke's cache preferences and localisation rules'''
        parts = filePath.split('/') # NUKE ALREADY CONVERTS BACK SLASHES TO FORWARD SLASHES ON WINDOWS
        if not filePath.startswith(os.sep):
            # DRIVE LETTER
            driveLetter = parts[0]
            parts = parts [1:] # REMOVE DRIVE LETTER FROM PARTS BECAUSE WE ARE STORING IT IN PREFIX
            prefix =  driveLetter.replace(':', '_')
        else:
            # REPLACE EACH LEADING SLASH WITH UNDERSCORE
            slashCount = len([i for i in parts if not i])
            root = [p for p in parts if p][0]
            parts = parts[slashCount + 1:] # REMOVE SLASHES AND ROOT FROM PARTS BECAUSE WE ARE STORING THOSE IN PREFIX
            prefix = '_' * slashCount + root

        # RE-ASSEMBLE TO LOCALISED PATH
        parts.insert(0, prefix)
        parts = self.cachePath.split('/') + parts
        return '/'.join(parts[:-1]) # RETURN LOCAL DIRECTORY USING FORWARD SLASHES TO BE CONSISTENT WITH NUKE




def fixPadding(path):
    '''
    Convert padding from hashes to C-like padding
    example:
       path.####.exr > path.%04d.exr
       path.######.exr > path.%06d.exr
    '''
    
    match = re.search('#+' , path)
    if not match:
        return path
    oldPadding = match.group(0)
    newPadding = '%%%sd' % str(len(oldPadding)).zfill(2)
    return re.sub(oldPadding, newPadding, path)


def getFrameList(fileKnob, existingFilePaths):
    '''
    Return a list of frames that are part of the sequence that fileKnob is ponting to.
    If the file path is already in existingFilePaths it will not be included.
    '''

    node = fileKnob.node()
    filePath = fixPadding(fileKnob.value())
    if os.path.isfile(filePath):
        # STILL FRAME, QUICKTIME, ETC
        if filePath not in existingFilePaths:
            return [filePath]
    first = node.firstFrame()
    last = node.lastFrame()
    return [filePath%i for i in xrange(first, last+1) if filePath%i not in existingFilePaths]
    

def localiseFileThreaded(readKnobList):
    '''Wrapper to duck punch default method'''

    fileDict = {}
    allFilesPaths = []
    for knob in readKnobList:
        first = knob.node().firstFrame()
        last = knob.node().lastFrame()
        filePathList = getFrameList(knob, allFilesPaths)
        fileDict[knob.node().name()] = filePathList
        allFilesPaths.extend(filePathList)

    localiseThread = LocaliseThreaded(fileDict)
    localiseThread.start()


def register():
    nuke.localiseFilesHOLD = nuke.localiseFiles #BACKUP ORIGINAL
    nuke.localiseFiles = localiseFileThreaded
    #doLocalise(True) # ONLY FOR DEBUGGING, THIS IS ACTUALLY CALLED FROM THE DEFAULT MENU

if __name__ == '__main__':
    pass

    
    


