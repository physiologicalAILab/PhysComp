# This Python file uses the following encoding: utf-8
import threading
import time
from tkinter import font
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.lines import Line2D
from matplotlib.animation import TimedAnimation
from matplotlib.figure import Figure
import sys
import os

from PySide6.QtWidgets import QApplication, QWidget, QGraphicsScene
from PySide6.QtCore import QFile, QObject, Signal
from PySide6.QtUiTools import QUiLoader

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
# matplotlib.rcParams["figure.autolayout"] = True
# matplotlib.use("Qt5Agg")
from utils.data_processing_lib import lFilter
from utils.devices import serialPort
import heartpy as hp
from datetime import datetime
import calendar
import copy

live_acquisition_flag = False
features_dict = {}

class PPG(QWidget):
    def __init__(self):
        super(PPG, self).__init__()
        self.load_ui()
        
    def load_ui(self):
        loader = QUiLoader()
        path = os.path.join(os.path.dirname(__file__), "form.ui")
        ui_file = QFile(path)
        ui_file.open(QFile.ReadOnly)
        self.ui = loader.load(ui_file, self)
        global features_dict

        self.ui.spObj = serialPort()
        self.ui.ser_port_names = []
        self.ui.curr_ser_port_name = ''
        for port, desc, hwid in sorted(self.ui.spObj.ports):
            # print("{}: {} [{}]".format(port, desc, hwid))
            self.ui.ser_port_names.append(port)

        self.ui.comboBox_comport.addItems(self.ui.ser_port_names)
        self.ui.curr_ser_port_name = self.ui.ser_port_names[0]
        self.ui.pushButton_connect.setEnabled(True)
        self.ui.label_status.setText("Serial port specified: " + self.ui.curr_ser_port_name)

        self.ui.comboBox_comport.currentIndexChanged.connect(self.update_serial_port)
        self.ui.pushButton_connect.pressed.connect(self.connect_serial_port)
        self.ui.pushButton_start_live_acquisition.pressed.connect(self.start_acquisition)
        self.ppgDataLoop_started = False

        self.ui.data_record_flag = False
        self.ui.data_root_dir = os.path.join(os.getcwd(), 'data')
        if not os.path.exists(self.ui.data_root_dir):
            os.makedirs(self.ui.data_root_dir)
        self.ui.pushButton_record_data.pressed.connect(self.record_data)
        self.ui.raw_ppg_signal = []

        self.ui.exp_names = [self.ui.comboBox_expName.itemText(i) for i in range(self.ui.comboBox_expName.count())]
        self.ui.utc_timestamp_featDict = datetime.utcnow()

        self.ui.curr_exp_name = self.ui.exp_names[0]
        self.ui.conditions = [self.ui.listWidget_expConditions.item(x).text() for x in range(self.ui.listWidget_expConditions.count())]
        for cnd in self.ui.conditions:
            features_dict[cnd] = {}
            features_dict[cnd]['bpm'] = np.array([0])
            features_dict[cnd]['sdnn'] = np.array([0])
            features_dict[cnd]['sdsd'] = np.array([0])
            features_dict[cnd]['ibi'] = np.array([0])
            features_dict[cnd]['rmssd'] = np.array([0])
            features_dict[cnd]['pnn50'] = np.array([0])

        # # Place the matplotlib figure
        self.ui.fs = 40
        self.myFig = LivePlotFigCanvas(uiObj=self.ui)
        self.graphic_scene = QGraphicsScene()
        self.graphic_scene.addWidget(self.myFig)
        self.ui.graphicsView.setScene(self.graphic_scene)
        self.ui.graphicsView.show()
        # Add the callbackfunc
        self.ppgDataLoop = threading.Thread(name='ppgDataLoop', target=ppgDataSendLoop, daemon=True, args=(
            self.addData_callbackFunc, self.ui.spObj))

        self.ui.listWidget_expConditions.currentItemChanged.connect(self.update_exp_condition)
        self.ui.curr_exp_condition = self.ui.listWidget_expConditions.currentRow()

        # # Place the matplotlib figure
        self.featFig = FeaturesFigCanvas(uiObj=self.ui)
        self.feat_graphic_scene = QGraphicsScene()
        self.feat_graphic_scene.addWidget(self.featFig)
        self.ui.graphicsView_2.setScene(self.feat_graphic_scene)
        self.ui.graphicsView_2.show()

        ui_file.close()

    def addData_callbackFunc(self, value):
        # print("Add data: " + str(value))
        self.myFig.addData(value)
      
        return

    def update_serial_port(self):
        self.ui.curr_ser_port_name = self.ui.ser_port_names[self.ui.comboBox_comport.currentIndex()]
        self.ui.label_status.setText("Serial port specified: " + self.ui.curr_ser_port_name)

    def connect_serial_port(self):
        open_status = self.ui.spObj.connectPort(self.ui.curr_ser_port_name)
        if open_status:
            self.ui.label_status.setText("Serial port is now connected: " + str(self.ui.spObj.ser))
            self.ui.pushButton_start_live_acquisition.setEnabled(True)

    def start_acquisition(self):
        global live_acquisition_flag
        if not live_acquisition_flag:
            live_acquisition_flag = True
            if not self.ppgDataLoop_started:
                self.ppgDataLoop.start()
                self.ppgDataLoop_started = True
                self.ui.label_status.setText("Live acquisition started. Select experiment and condition to start recording data.")
            else:
                self.ui.label_status.setText("Live acquisition started.")
            self.ui.comboBox_expName.setEnabled(True)
            self.ui.listWidget_expConditions.setEnabled(True)
            # self.ui.pushButton_record_data.setEnabled(True)
            self.ui.pushButton_start_live_acquisition.setText('Stop Live Acquisition')
        else:
            self.ui.label_status.setText("Live acquisition stopped.")
            live_acquisition_flag = False
            self.ui.pushButton_record_data.setEnabled(False)
            self.ui.pushButton_start_live_acquisition.setText('Start Live Acquisition')

    def update_exp_condition(self):
        self.ui.curr_exp_condition = self.ui.conditions[self.ui.listWidget_expConditions.currentRow()]
        self.ui.label_status.setText("Experiment Condition Selected: " + self.ui.curr_exp_condition)
        self.ui.pushButton_record_data.setEnabled(True)

    def record_data(self):
        if not self.ui.data_record_flag:
            self.ui.data_record_flag = True
            self.ui.utc_timestamp_signal = datetime.utcnow()
            self.ui.pushButton_record_data.setText("Stop Recording")
        else:
            th = threading.Thread(target=self.save_raw_signal)
            th.start()
            self.ui.data_record_flag = False
            self.ui.pushButton_record_data.setText("Start Recording")


    def save_raw_signal(self):
        global features_dict
        arr = np.array(self.ui.raw_ppg_signal)
        fname_signal = os.path.join(self.ui.data_root_dir, self.ui.curr_exp_name + '_' + self.ui.curr_exp_condition + '_' +
                            'raw_signal_' + str(calendar.timegm(self.ui.utc_timestamp_signal.timetuple())) + '.npy')
        np.save(fname_signal, arr)

        fname_featDict = os.path.join(self.ui.data_root_dir, self.ui.curr_exp_name + '_' +
                             'featDict_' + str(calendar.timegm(self.ui.utc_timestamp_featDict.timetuple())) + '.npy')
        np.save(fname_featDict, features_dict)
        self.ui.raw_ppg_signal = []

class LivePlotFigCanvas(FigureCanvas, TimedAnimation):
    def __init__(self, uiObj):
        self.uiObj = uiObj
        self.addedData = []
        self.abc = 0
        # print(matplotlib.__version__)
        # The data
        self.max_time = 20
        self.measure_time = 5
        self.xlim = self.max_time*self.uiObj.fs
        self.n = np.linspace(0, self.xlim - 1, self.xlim)
        self.y = (self.n * 0.0) + 50
        self.n = self.n/self.uiObj.fs
        # The window
        self.fig = Figure(figsize=(25,5), dpi=50)
        self.ax1 = self.fig.add_subplot(111)
        # self.ax1 settings
        self.ax1.set_xlabel('Time (seconds)', fontsize=18)
        self.ax1.set_ylabel('PPG Signal', fontsize=18)
        self.line1 = Line2D([], [], color='blue')
        self.line1_tail = Line2D([], [], color='red', linewidth=2)
        self.line1_head = Line2D([], [], color='red', marker='o', markeredgecolor='r')
        self.ax1.add_line(self.line1)
        self.ax1.add_line(self.line1_tail)
        self.ax1.add_line(self.line1_head)
        self.ax1.set_xlim(0, self.max_time)
        self.ax1.set_ylim(-100, 200)

        # Hide the right and top spines
        self.ax1.spines['right'].set_visible(False)
        self.ax1.spines['top'].set_visible(False)

        # Only show ticks on the left and bottom spines
        self.ax1.yaxis.set_ticks_position('left')
        self.ax1.xaxis.set_ticks_position('bottom')

        FigureCanvas.__init__(self, self.fig)
        TimedAnimation.__init__(self, self.fig, interval=int(round(1000.0/self.uiObj.fs)), blit = True)

        lowcut = 0.5
        highcut = 5.0
        filt_order = 2
        self.filtObj = lFilter(lowcut, highcut, self.uiObj.fs, order=filt_order)
        self.count_frame = 0# self.max_time * self.uiObj.fs
        return

    def new_frame_seq(self):
        return iter(range(self.n.size))

    def _init_draw(self):
        lines = [self.line1, self.line1_tail, self.line1_head]
        for l in lines:
            l.set_data([], [])
        return

    def addData(self, value):
        filtered_value = self.filtObj.lfilt(value)
        # self.addedData.append(value)
        self.addedData.append(filtered_value)
        if self.uiObj.data_record_flag:
            self.uiObj.raw_ppg_signal.append(filtered_value)
        return

    def _step(self, *args):
        # Extends the _step() method for the TimedAnimation class.
        try:
            TimedAnimation._step(self, *args)
        except Exception as e:
            self.abc += 1
            print(str(self.abc))
            TimedAnimation._stop(self)
            pass
        return

    def _draw_frame(self, framedata):
        margin = 2
        while(len(self.addedData) > 0):
            self.y = np.roll(self.y, -1)
            self.y[-1] = self.addedData[-1]
            del(self.addedData[0])
            self.count_frame += 1

        if self.count_frame >= (self.measure_time * self.uiObj.fs):
            self.count_frame = 0
            self.ax1.set_ylim(np.min(self.y[-self.measure_time*self.uiObj.fs:]), np.max(
                self.y[-self.measure_time*self.uiObj.fs:]))
            if self.uiObj.data_record_flag:
                th = threading.Thread(target=self.compute_ppg_features)
                th.start()
        self.line1.set_data(self.n[ 0 : self.n.size - margin ], self.y[ 0 : self.n.size - margin ])
        self.line1_tail.set_data(np.append(self.n[-10:-1 - margin], self.n[-1 - margin]), np.append(self.y[-10:-1 - margin], self.y[-1 - margin]))
        self.line1_head.set_data(self.n[-1 - margin], self.y[-1 - margin])
        self._drawn_artists = [self.line1, self.line1_tail, self.line1_head]
        return

    def compute_ppg_features(self):
        global features_dict
        wd, m = hp.process(self.y, sample_rate=self.uiObj.fs)
        # for key, measure in m.items():
        #     print(key, measure)

        features_dict[self.uiObj.curr_exp_condition]['bpm'] = np.append(features_dict[self.uiObj.curr_exp_condition]['bpm'], m['bpm'])
        features_dict[self.uiObj.curr_exp_condition]['sdnn'] = np.append(features_dict[self.uiObj.curr_exp_condition]['sdnn'], m['sdnn'])
        features_dict[self.uiObj.curr_exp_condition]['sdsd'] = np.append(features_dict[self.uiObj.curr_exp_condition]['sdsd'], m['sdsd'])
        features_dict[self.uiObj.curr_exp_condition]['ibi'] = np.append(features_dict[self.uiObj.curr_exp_condition]['ibi'], m['ibi'])
        features_dict[self.uiObj.curr_exp_condition]['rmssd'] = np.append(features_dict[self.uiObj.curr_exp_condition]['rmssd'], m['rmssd'])
        features_dict[self.uiObj.curr_exp_condition]['pnn50'] = np.append(features_dict[self.uiObj.curr_exp_condition]['pnn50'], m['pnn50'])


class FeaturesFigCanvas(FigureCanvas, TimedAnimation):
    def __init__(self, uiObj):
        self.uiObj = uiObj
        # print(matplotlib.__version__)

        self.update_time = 1
        conditions = [self.uiObj.listWidget_expConditions.item(x).text() for x in range(self.uiObj.listWidget_expConditions.count())]
        self.y_pos = np.arange(len(conditions))

        self.bpm = [0, 0, 0]
        self.sdnn = [0, 0, 0]
        self.sdsd = [0, 0, 0]
        self.ibi = [0, 0, 0]
        self.rmssd = [0, 0, 0]
        self.pnn50 = [0, 0, 0]

        self.min_bpm = 0
        self.min_sdnn = 0
        self.min_sdsd = 0
        self.min_ibi = 0
        self.min_rmssd = 0
        self.min_pnn50 = 0

        self.max_bpm = 0.1
        self.max_sdnn = 0.1
        self.max_sdsd = 0.1
        self.max_ibi = 0.1
        self.max_rmssd = 0.1
        self.max_pnn50 = 0.1

        self.n = np.linspace(0, self.uiObj.fs, self.uiObj.fs)

        # The window
        self.fig = Figure(figsize=(10, 5), dpi=50)
        self.category_colors = plt.get_cmap('nipy_spectral')(np.linspace(0, 1, len(conditions)))

        self.ax1 = self.fig.add_subplot(231)
        self.bpm_bar = self.ax1.barh(self.y_pos, self.bpm, color=self.category_colors)
        self.ax1.set_yticks(self.y_pos, labels=conditions)
        self.ax1.set_xlabel('Pulse Rate', fontsize=14)
        self.ax1.invert_xaxis()
        self.ax1.invert_yaxis()
        # Hide the right and top spines
        self.ax1.spines['right'].set_visible(False)
        self.ax1.spines['top'].set_visible(False)
        # Only show ticks on the left and bottom spines
        self.ax1.yaxis.set_ticks_position('left')
        self.ax1.xaxis.set_ticks_position('bottom')

        self.ax2 = self.fig.add_subplot(232)
        self.sdnn_bar = self.ax2.barh(self.y_pos, self.sdnn, color=self.category_colors)
        self.ax2.set_yticks(self.y_pos, labels=conditions)
        self.ax2.set_xlabel('SDNN', fontsize=14)
        self.ax2.invert_xaxis()
        self.ax2.invert_yaxis()
        # Hide the right and top spines
        self.ax2.spines['right'].set_visible(False)
        self.ax2.spines['top'].set_visible(False)
        # Only show ticks on the left and bottom spines
        self.ax2.yaxis.set_ticks_position('left')
        self.ax2.xaxis.set_ticks_position('bottom')

        self.ax3 = self.fig.add_subplot(233)
        self.sdsd_bar = self.ax3.barh(self.y_pos, self.sdsd, color=self.category_colors)
        self.ax3.set_yticks(self.y_pos, labels=conditions)
        self.ax3.set_xlabel('SDSD', fontsize=14)
        self.ax3.invert_xaxis()
        self.ax3.invert_yaxis()
        # Hide the right and top spines
        self.ax3.spines['right'].set_visible(False)
        self.ax3.spines['top'].set_visible(False)
        # Only show ticks on the left and bottom spines
        self.ax3.yaxis.set_ticks_position('left')
        self.ax3.xaxis.set_ticks_position('bottom')

        self.ax4 = self.fig.add_subplot(234)
        self.pnn50_bar = self.ax4.barh(self.y_pos, self.pnn50, color=self.category_colors)
        self.ax4.set_yticks(self.y_pos, labels=conditions)
        self.ax4.set_xlabel('pNN50', fontsize=14)
        self.ax4.invert_yaxis()
        self.ax4.invert_xaxis()
        # Hide the right and top spines
        self.ax4.spines['right'].set_visible(False)
        self.ax4.spines['top'].set_visible(False)
        # Only show ticks on the left and bottom spines
        self.ax4.yaxis.set_ticks_position('left')
        self.ax4.xaxis.set_ticks_position('bottom')

        self.ax5 = self.fig.add_subplot(235)
        self.rmssd_bar = self.ax5.barh(self.y_pos, self.rmssd, color=self.category_colors)
        self.ax5.set_yticks(self.y_pos, labels=conditions)
        self.ax5.set_xlabel('RMSSD', fontsize=14)
        self.ax5.invert_yaxis()
        self.ax5.invert_xaxis()
        # Hide the right and top spines
        self.ax5.spines['right'].set_visible(False)
        self.ax5.spines['top'].set_visible(False)
        # Only show ticks on the left and bottom spines
        self.ax5.yaxis.set_ticks_position('left')
        self.ax5.xaxis.set_ticks_position('bottom')

        self.ax6 = self.fig.add_subplot(236)
        self.ibi_bar = self.ax6.barh(self.y_pos, self.ibi, color=self.category_colors)
        self.ax6.set_yticks(self.y_pos, labels=conditions)
        self.ax6.set_xlabel('IBI', fontsize=14)
        self.ax6.invert_yaxis()
        self.ax6.invert_xaxis()
        # Hide the right and top spines
        self.ax6.spines['right'].set_visible(False)
        self.ax6.spines['top'].set_visible(False)
        # Only show ticks on the left and bottom spines
        self.ax6.yaxis.set_ticks_position('left')
        self.ax6.xaxis.set_ticks_position('bottom')

        # self.fig.set_title('Physiological parameters under different experimental conditions')
        self.fig.tight_layout()

        FigureCanvas.__init__(self, self.fig)
        TimedAnimation.__init__(self, self.fig, interval=int(round(self.update_time * 1000.0/self.uiObj.fs)), blit = True)
        # TimedAnimation.__init__(self, self.fig, interval=1, blit = True)

        return

    def new_frame_seq(self):
        return iter(range(self.n.size))

    def _step(self, *args):
        # Extends the _step() method for the TimedAnimation class.
        try:
            TimedAnimation._step(self, *args)
        except Exception as e:
            TimedAnimation._stop(self)
            pass
        return

    def _init_draw(self):
        return

    def _draw_frame(self, framedata):
        th = threading.Thread(target=self.prepare_bar_plot)
        th.start()
        return

    def prepare_bar_plot(self):
        global features_dict

        self.bpm = []
        self.sdnn = []
        self.sdsd = []
        self.ibi = []
        self.rmssd = []
        self.pnn50 = []

        for cnd in self.uiObj.conditions:
            self.bpm.append(np.median(features_dict[cnd]['bpm']))
            self.sdnn.append(np.median(features_dict[cnd]['sdnn']))
            self.sdsd.append(np.median(features_dict[cnd]['sdsd']))
            self.ibi.append(np.median(features_dict[cnd]['ibi']))
            self.rmssd.append(np.median(features_dict[cnd]['rmssd']))
            self.pnn50.append(np.median(features_dict[cnd]['pnn50']))

        try:
            if self.min_bpm >= np.min(self.bpm):
                self.min_bpm = np.min(self.bpm)
            if self.min_sdnn >= np.min(self.sdnn):
                self.min_sdnn = np.min(self.sdnn)
            if self.min_sdsd >= np.min(self.sdsd):
                self.min_sdsd = np.min(self.sdsd)
            if self.min_ibi >= np.min(self.ibi):
                self.min_ibi = np.min(self.ibi)
            if self.min_rmssd >= np.min(self.rmssd):
                self.min_rmssd = np.min(self.rmssd)
            if self.min_pnn50 >= np.min(self.pnn50):
                self.min_pnn50 = np.min(self.pnn50)

            if self.max_bpm <= np.max(self.bpm):
                self.max_bpm = np.max(self.bpm)
            if self.max_sdnn <= np.max(self.sdnn):
                self.max_sdnn = np.max(self.sdnn)
            if self.max_sdsd <= np.max(self.sdsd):
                self.max_sdsd = np.max(self.sdsd)
            if self.max_ibi <= np.max(self.ibi):
                self.max_ibi = np.max(self.ibi)
            if self.max_rmssd <= np.max(self.rmssd):
                self.max_rmssd = np.max(self.rmssd)
            if self.max_pnn50 <= np.max(self.pnn50):
                self.max_pnn50 = np.max(self.pnn50)
        except:
            pass

        for i in range(len(self.bpm)):
            self.bpm_bar[i].set_width(self.bpm[i])
            self.sdnn_bar[i].set_width(self.sdnn[i])
            self.sdsd_bar[i].set_width(self.sdsd[i])
            self.pnn50_bar[i].set_width(self.pnn50[i])
            self.rmssd_bar[i].set_width(self.rmssd[i])
            self.ibi_bar[i].set_width(self.ibi[i])

        self.ax1.set_xlim(self.min_bpm, self.max_bpm)
        self.ax2.set_xlim(self.min_sdnn, self.max_sdnn)
        self.ax3.set_xlim(self.min_sdsd, self.max_sdsd)
        self.ax4.set_xlim(self.min_pnn50, self.max_pnn50)
        self.ax5.set_xlim(self.min_rmssd, self.max_rmssd)
        self.ax6.set_xlim(self.min_ibi, self.max_ibi)

        return

# Setup a signal slot mechanism, to send data to GUI in a thread-safe way.
class Communicate(QObject):
    data_signal = Signal(float)


def ppgDataSendLoop(addData_callbackFunc, spObj):
    global live_acquisition_flag
    # Setup the signal-slot mechanism.
    mySrc = Communicate()
    mySrc.data_signal.connect(addData_callbackFunc)
    ppgVal = 0

    while(True):
        if live_acquisition_flag:
            #Read data from serial port
            serial_data = spObj.ser.readline()
            serial_data = serial_data.split(b'\r\n')
            # print(serial_data)

            try:
                ppgVal = float(serial_data[0])
            except:
                ppgVal = ppgVal

            time.sleep(0.01)
            mySrc.data_signal.emit(ppgVal)  # <- Here you emit a signal!l

def main(app):
    # app.setStyle('Fusion')
    widget = PPG()
    widget.show()
    ret = app.exec()
    del widget
    # sys.exit(ret)
    return

if __name__ == '__main__':
    # Create the application instance.
    app = QApplication([])
    main(app)