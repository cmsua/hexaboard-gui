from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor, QPalette
from PyQt6.QtWidgets import QWidget, QFormLayout, QVBoxLayout, QHBoxLayout, QTabWidget, QTextEdit, QLabel, QPushButton
from misc_widgets import ScannerLineEdit

from config import config

import os, logging, subprocess, io, re
from functools import partial

from ansi2html import Ansi2HTMLConverter
console_color = re.compile(r"\[\d{,3}(;\d{,3}){4}m")
converter = Ansi2HTMLConverter()

class TestingArea(QWidget):
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout()

        # Label
        label = QLabel("Testing Data")
        font = label.font()
        font.setPointSize(font.pointSize() * 1.5)
        label.setFont(font)
        layout.addWidget(label)
        
        # Data Section, Tabs below
        dataLayout = QHBoxLayout()
        tabs = QTabWidget()
        
        hexacontrollers = config.getHexacontrollers()
        self.textAreas = {}
        for controller in hexacontrollers:
            # Create log text area
            textArea = QTextEdit()
            textArea.setReadOnly(True)
            pallete = textArea.palette()
            pallete.setColor(QPalette.ColorRole.Base, 0)
            textArea.setPalette(pallete)

            self.textAreas[controller] = textArea
            tabs.addTab(textArea, config.getHexacontrollerName(controller))

            # Create Column
            def insertLine(controller: str, line: str) -> None:
                self.textAreas[controller].moveCursor(QTextCursor.MoveOperation.End)
                self.textAreas[controller].insertHtml(line)
                self.textAreas[controller].moveCursor(QTextCursor.MoveOperation.End)

            column = InputColumn(controller)
            column.line.connect(partial(insertLine, controller))
            column.clear.connect(textArea.clear)
            dataLayout.addWidget(column)
        
        dataLayout.addStretch()
        dataWidget = QWidget()
        dataWidget.setLayout(dataLayout)
        layout.addWidget(dataWidget)

        layout.addWidget(tabs)

        # Finish Setup

        layout.addStretch()

        self.setLayout(layout)

# A column that asks for all applicable inputs
class InputColumn(QWidget):
    line = pyqtSignal(str)
    clear = pyqtSignal()

    def __init__(self, hexacontrollerId: str) -> None:
        super().__init__()
        self.id = hexacontrollerId
        layout = QFormLayout()

        # Title
        title = QLabel(config.getHexacontrollerName(self.id))
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addRow(title)

        # Add Labels
        self.boardField = ScannerLineEdit()
        self.boardField.setMaxLength(15)
        layout.addRow("Board ID", self.boardField)

        self.hgroc0Field = ScannerLineEdit()
        self.hgroc0Field.setMaxLength(15)
        layout.addRow("HGROC 0", self.hgroc0Field)

        self.hgroc1Field = ScannerLineEdit()
        self.hgroc1Field.setMaxLength(15)
        layout.addRow("HGROC 1", self.hgroc1Field)

        self.hgroc2Field = ScannerLineEdit()
        self.hgroc2Field.setMaxLength(15)
        layout.addRow("HGROC 2", self.hgroc2Field)

        # Start Tests
        self.button = QPushButton("Start Test")
        self.button.clicked.connect(self.start_test)
        layout.addRow(self.button)

        self.setLayout(layout)

    def start_test(self) -> None:
        logger = logging.getLogger(self.id)
        logger.info("Starting test for " + self.id)
        
        # Disable inputs till test is concluded
        self.boardField.setEnabled(False)
        self.hgroc0Field.setEnabled(False)
        self.hgroc1Field.setEnabled(False)
        self.hgroc2Field.setEnabled(False)

        self.button.setText("Test In Progress...")
        self.button.setEnabled(False)

        # Create Config
        testConfigPath = os.path.join(config.getTestConfigDir(), self.id + ".yaml")
        with open(config.getTestConfigTemplate(), "r") as template:
            testConfig = template.read()
            # Replace key values
            testConfig = testConfig.replace("TOKEN_BOARD_BARCODE", self.boardField.text())
            testConfig = testConfig.replace("TOKEN_ROC_0_BARCODE", self.hgroc0Field.text())
            testConfig = testConfig.replace("TOKEN_ROC_1_BARCODE", self.hgroc1Field.text())
            testConfig = testConfig.replace("TOKEN_ROC_2_BARCODE", self.hgroc2Field.text())
            testConfig = testConfig.replace("TOKEN_HEXACONTROLLER_ADDRESS", config.getHexacontrollerAddress(self.id))
            testConfig = testConfig.replace("TOKEN_ZYNQ_PORT", config.getHexacontrollerZynqPort(self.id))
            testConfig = testConfig.replace("TOKEN_DAQ_CLIENT_PORT", config.getHexacontrollerDaqClientPort(self.id))
            testConfig = testConfig.replace("TOKEN_DAQ_SERVER_PORT", config.getHexacontrollerDaqServerPort(self.id))
            testConfig = testConfig.replace("TOKEN_I2C_SERVER_PORT", config.getHexacontrollerI2CServerPort(self.id))
            testConfig = testConfig.replace("TOKEN_OUTDIR", config.getOutputDir())

            # Create Config Dir if not exists
            if not os.path.exists(config.getTestConfigDir()):
                logger.info(f"Test config directory does not exist. Creating {config.getTestConfigDir()}")
                os.makedirs(config.getTestConfigDir())

            # Write Config
            with open(testConfigPath, "w") as testConfigFile:
                testConfigFile.write(testConfig)
                logger.debug(f"Written test config to {testConfigFile}")

        # Create Output Dir if not exists
        if not os.path.exists(config.getOutputDir()):
            logger.info(f"Output directory does not exist. Creating {config.getOutputDir()}")
            os.makedirs(config.getOutputDir())

        # Start Tests
        self.clear.emit()
        self.test_thread = TestThread(logger, testConfigPath, config.getHexacontrollerDaqClientPort(self.id), self.boardField.text())
        self.test_thread.finished.connect(self.enable_tests)
        self.test_thread.line.connect(self.new_line)
        self.test_thread.start()

    # Called each time the test script prints a new line
    def new_line(self, line: str) -> None:
        self.line.emit(line)
        
    # Re-Enable Tests after finished
    def enable_tests(self) -> None:
        self.boardField.setEnabled(True)
        self.hgroc0Field.setEnabled(True)
        self.hgroc1Field.setEnabled(True)
        self.hgroc2Field.setEnabled(True)

        self.button.setText("Start Test")
        self.button.setEnabled(True)

# Thread to run tests
# Ran in the background to not cause messes
class TestThread(QThread):
    # Line Out Signal
    line = pyqtSignal(str)

    def __init__(self, logger, config, daqClientPort, name):
        super().__init__()
        self.logger = logger
        self.config = config
        self.daqClientPort = daqClientPort
        self.name = name
    
    def run(self) -> None:
        # Recreate environment and such
        env = os.environ.copy()
        env["PATH"] = f"{ config.getHexactrlSoftwareDir() }/bin:{ env['PATH'] }"
        env["PYTHONPATH"] = f"{ config.getHexactrlScriptDir() }/analysis:{ config.getHexactrlScriptDir() }"
        env["SOURCE"] = f"{ config.getHexactrlScriptDir() }/analysis/etc/env.sh"
        env["BASEDIR"] = f"{ config.getHexactrlScriptDir() }/analysis"

        # Start Processes
        self.logger.debug("Starting DAQ Client")
        daqClientLog = open(f"{ config.getOutputDir() }/{ self.name }-daq-client.log", "w")
        daqClient = subprocess.Popen([ f"{ config.getHexactrlSoftwareDir() }/bin/daq-client", "-p", str(self.daqClientPort) ], stdout=daqClientLog, stderr=subprocess.STDOUT)
        
        self.logger.debug("Starting Test Process")
        testLog = open(f"{ config.getOutputDir() }/{ self.name }.log", "w")
        proc = subprocess.Popen([ "./venv/bin/python3", "hexaboard-V3B-production-test-ua.py", "-i", self.config ], env=env, cwd=config.getHexactrlScriptDir(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        # Read process till done
        for line in io.TextIOWrapper(proc.stdout, encoding='utf-8'):
            line = console_color.sub("", line)

            testLog.write(line)
            self.line.emit(converter.convert(line))

        self.logger.info("Tests Finished, Killing Daq Client")
        daqClient.kill()

        daqClientLog.close()
        testLog.close()