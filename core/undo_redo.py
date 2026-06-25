"""Undo/Redo manager using Command pattern."""

from dataclasses import dataclass
from typing import Callable, List, Optional
from utils.constants import MAX_UNDO_STACK


@dataclass
class Command:
    """Represents an undoable command."""
    description: str
    execute: Callable
    undo: Callable


class CompoundCommand(Command):
    """A command that groups multiple operations."""
    
    def __init__(self, description: str, commands: List[Command]):
        self.description = description
        self._commands = commands
        
        def do_all():
            for cmd in self._commands:
                cmd.execute()
        
        def undo_all():
            for cmd in reversed(self._commands):
                cmd.undo()
        
        self.execute = do_all
        self.undo = undo_all


class UndoRedoManager:
    """Manages undo/redo stacks."""
    
    def __init__(self, max_size: int = MAX_UNDO_STACK):
        self._undo_stack: List[Command] = []
        self._redo_stack: List[Command] = []
        self._max_size = max_size
    
    def execute(self, command: Command):
        """Execute a command and add to undo stack."""
        command.execute()
        self._record(command)
    
    def record(self, command: Command):
        """Record an already-applied command without running execute again."""
        self._record(command)
    
    def _record(self, command: Command):
        self._undo_stack.append(command)
        self._redo_stack.clear()
        while len(self._undo_stack) > self._max_size:
            self._undo_stack.pop(0)
    
    def undo(self) -> Optional[str]:
        """Undo last command. Returns description or None."""
        if not self._undo_stack:
            return None
        
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        return command.description
    
    def redo(self) -> Optional[str]:
        """Redo last undone command. Returns description or None."""
        if not self._redo_stack:
            return None
        
        command = self._redo_stack.pop()
        command.execute()
        self._undo_stack.append(command)
        return command.description
    
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0
    
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0
    
    def clear(self):
        """Clear both stacks."""
        self._undo_stack.clear()
        self._redo_stack.clear()
    
    @property
    def undo_description(self) -> str:
        """Get description of next undo command."""
        if self._undo_stack:
            return self._undo_stack[-1].description
        return ''
    
    @property
    def redo_description(self) -> str:
        """Get description of next redo command."""
        if self._redo_stack:
            return self._redo_stack[-1].description
        return ''
