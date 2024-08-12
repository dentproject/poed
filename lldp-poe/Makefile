TARGET_EXEC ?= lldp-poed
CC := aarch64-linux-gnu-gcc
BUILD_DIR ?= ./build
SRC_DIRS ?= ./src ./include ./lib

SRCS := $(shell find $(SRC_DIRS) -name *.cpp -or -name *.c -or -name *.s)
OBJS := $(SRCS:%=$(BUILD_DIR)/%.o)
DEPS := $(OBJS:.o=.d)

INC_DIRS := ./ ./lib /usr/local/include
INC_FLAGS := $(addprefix -I,$(INC_DIRS))

CPPFLAGS ?= -pthread $(INC_FLAGS) -MMD -MP -Wall -Werror

# TODO: Use vendor submodules instead of relying on local setup.
LIBDIRS := /lib /usr/lib /usr/local/lib
LIBPATH := $(addprefix -L,$(LIBDIRS))
LDFLAGS := -llldpctl

$(BUILD_DIR)/$(TARGET_EXEC): $(OBJS)
	$(CC) $(OBJS) -o $@ $(LIBPATH) $(LDFLAGS) -pthread -Wall -Werror

# Assembly files
$(BUILD_DIR)/%.s.o: %.s
	$(MKDIR_P) $(dir $@)
	$(AS) $(ASFLAGS) -c $< -o $@

# C source files
$(BUILD_DIR)/%.c.o: %.c
	$(MKDIR_P) $(dir $@)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

.PHONY: clean

clean:
	$(RM) -r $(BUILD_DIR)

-include $(DEPS)

MKDIR_P ?= mkdir -p
